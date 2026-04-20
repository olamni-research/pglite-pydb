#!/usr/bin/env node
/*
 * PGlite <-> Postgres-wire bridge (T013 — TCP branch only).
 *
 * Binds EXACTLY ONE listener per process, selected by --transport:
 *   --transport tcp  -> net.createServer().listen(port, host)
 *   --transport pipe -> TODO (T027 / Slice US2); see stub below.
 *
 * Before forwarding any bytes to PGlite, the bridge parses the client's
 * StartupMessage, extracts the `user` field, and rejects any role that is
 * not `example_user` with a FATAL ErrorResponse (FR-021). SSLRequest is
 * answered with 'N' (no SSL) and the bridge then waits for the real
 * StartupMessage on the same connection.
 *
 * Observability (contracts/transport.md):
 *   [bridge] start transport=<tcp|pipe> listen=<addr> data_dir=<abs>
 *   [bridge] accept transport=<tcp|pipe> peer=<pid-or-"-"> role=<r> result=<accept|reject>
 */

'use strict';

const net = require('net');
const path = require('path');
const { PGlite } = require('@electric-sql/pglite');

const ALLOWED_ROLE = 'example_user';

// -----------------------------------------------------------------------
// CLI parsing
// -----------------------------------------------------------------------

function parseArgs(argv) {
    const out = {
        transport: null,
        host: '127.0.0.1',
        port: null,
        pipeName: null,
        dataDir: null,
    };
    for (let i = 2; i < argv.length; i++) {
        const a = argv[i];
        const take = () => {
            const v = argv[++i];
            if (v === undefined) {
                console.error(`[bridge] missing value for ${a}`);
                process.exit(64);
            }
            return v;
        };
        switch (a) {
            case '--transport':  out.transport = take(); break;
            case '--host':       out.host = take(); break;
            case '--port':       out.port = parseInt(take(), 10); break;
            case '--pipe-name':  out.pipeName = take(); break;
            case '--data-dir':   out.dataDir = take(); break;
            default:
                console.error(`[bridge] unknown arg: ${a}`);
                process.exit(64);
        }
    }
    if (!out.transport || !out.dataDir) {
        console.error('[bridge] --transport and --data-dir are required');
        process.exit(64);
    }
    if (out.transport === 'tcp' && (!out.port || Number.isNaN(out.port))) {
        console.error('[bridge] --port is required for --transport tcp');
        process.exit(64);
    }
    return out;
}

// -----------------------------------------------------------------------
// Postgres wire-protocol helpers
// -----------------------------------------------------------------------

// Returns one of:
//   { kind: 'incomplete' }                       — need more bytes
//   { kind: 'ssl',    consumed }                 — SSLRequest (proto 1234/5679)
//   { kind: 'gssenc', consumed }                 — GSSENCRequest (1234/5680)
//   { kind: 'cancel', consumed }                 — CancelRequest (1234/5678)
//   { kind: 'startup', role, consumed, bytes }   — real StartupMessage
function parseStartupFrame(buf) {
    if (buf.length < 8) return { kind: 'incomplete' };
    const len = buf.readUInt32BE(0);
    if (len < 8 || len > 1 << 20) {
        return { kind: 'malformed' };
    }
    if (buf.length < len) return { kind: 'incomplete' };

    const protoMajor = buf.readUInt16BE(4);
    const protoMinor = buf.readUInt16BE(6);
    if (protoMajor === 1234) {
        const kind =
            protoMinor === 5679 ? 'ssl' :
            protoMinor === 5680 ? 'gssenc' :
            protoMinor === 5678 ? 'cancel' : 'unknown-special';
        return { kind, consumed: len };
    }

    // Real StartupMessage: parse null-terminated key/value pairs from offset 8.
    let i = 8;
    let role = null;
    while (i < len) {
        const keyEnd = buf.indexOf(0, i);
        if (keyEnd < 0 || keyEnd >= len) break;
        const key = buf.slice(i, keyEnd).toString('utf8');
        if (key === '') break;
        const valStart = keyEnd + 1;
        const valEnd = buf.indexOf(0, valStart);
        if (valEnd < 0 || valEnd >= len) break;
        const val = buf.slice(valStart, valEnd).toString('utf8');
        if (key === 'user') role = val;
        i = valEnd + 1;
    }
    return {
        kind: 'startup',
        role,
        consumed: len,
        bytes: buf.slice(0, len),
    };
}

// Emit an ErrorResponse followed by a graceful close.
function sendFatalRole(socket, role) {
    const msg = `role "${role}" is not permitted on this example server`;
    const fields = Buffer.concat([
        Buffer.from('S'), Buffer.from('FATAL\0'),
        Buffer.from('C'), Buffer.from('28000\0'),
        Buffer.from('M'), Buffer.from(msg + '\0'),
        Buffer.from([0]),
    ]);
    const frame = Buffer.alloc(5 + fields.length);
    frame[0] = 0x45; // 'E'
    frame.writeUInt32BE(4 + fields.length, 1);
    fields.copy(frame, 5);
    socket.end(frame);
}

// Split a buffer of regular (typed) frontend messages into complete frames.
// Returns { frames: Buffer[], rest: Buffer }.
function splitFrames(buf) {
    const frames = [];
    let off = 0;
    while (buf.length - off >= 5) {
        const len = buf.readUInt32BE(off + 1);
        if (len < 4 || buf.length - off < 1 + len) break;
        frames.push(buf.slice(off, off + 1 + len));
        off += 1 + len;
    }
    return { frames, rest: buf.slice(off) };
}

// -----------------------------------------------------------------------
// Connection handler
// -----------------------------------------------------------------------

function handleConnection(db, transport, socket) {
    let preStartupBuf = Buffer.alloc(0);
    let postBuf = Buffer.alloc(0);
    let gated = false;

    const logAccept = (role, result) => {
        const peer = '-';  // PID lookup across loopback is non-trivial; stub as "-"
        console.log(
            `[bridge] accept transport=${transport} peer=${peer} ` +
            `role=${role} result=${result}`
        );
    };

    const forward = async (bytes) => {
        try {
            const reply = await db.execProtocolRaw(new Uint8Array(bytes));
            if (reply && reply.length) {
                socket.write(Buffer.from(reply));
            }
        } catch (err) {
            console.error(`[bridge] protocol error: ${err && err.message}`);
            socket.destroy();
        }
    };

    const onDataPreStartup = (chunk) => {
        preStartupBuf = Buffer.concat([preStartupBuf, chunk]);
        // Loop: a single packet may carry SSLRequest + StartupMessage.
        // eslint-disable-next-line no-constant-condition
        while (true) {
            const parsed = parseStartupFrame(preStartupBuf);
            if (parsed.kind === 'incomplete') return;
            if (parsed.kind === 'malformed') {
                socket.destroy();
                return;
            }
            if (parsed.kind === 'ssl' || parsed.kind === 'gssenc') {
                // Tell client we don't support SSL/GSS encryption;
                // it should proceed with a plain StartupMessage next.
                socket.write(Buffer.from('N'));
                preStartupBuf = preStartupBuf.slice(parsed.consumed);
                continue;
            }
            if (parsed.kind === 'cancel') {
                // We don't implement cancel routing; drop the connection.
                socket.end();
                return;
            }
            if (parsed.kind === 'unknown-special') {
                socket.destroy();
                return;
            }
            // Real StartupMessage.
            const role = parsed.role || '';
            if (role !== ALLOWED_ROLE) {
                logAccept(role, 'reject');
                sendFatalRole(socket, role);
                return;
            }
            logAccept(role, 'accept');
            gated = true;
            socket.removeListener('data', onDataPreStartup);
            socket.on('data', onDataPost);
            // Forward the startup bytes, then any already-buffered tail.
            const startupBytes = parsed.bytes;
            const tail = preStartupBuf.slice(parsed.consumed);
            preStartupBuf = Buffer.alloc(0);
            forward(startupBytes).then(() => {
                if (tail.length) onDataPost(tail);
            });
            return;
        }
    };

    const onDataPost = (chunk) => {
        postBuf = Buffer.concat([postBuf, chunk]);
        const { frames, rest } = splitFrames(postBuf);
        postBuf = rest;
        (async () => {
            for (const f of frames) {
                await forward(f);
            }
        })();
    };

    socket.on('data', onDataPreStartup);
    socket.on('error', () => socket.destroy());
    socket.on('close', () => { /* nothing — PGlite has no per-socket state */ });
}

// -----------------------------------------------------------------------
// Main
// -----------------------------------------------------------------------

async function main() {
    const args = parseArgs(process.argv);
    const dataDirAbs = path.resolve(args.dataDir);
    const db = await PGlite.create({ dataDir: dataDirAbs });

    if (args.transport === 'pipe') {
        // TODO(T027 / Slice US2): implement Windows named-pipe transport.
        //   - listen path: `\\\\.\\pipe\\${pipeName}` (honor --unique-pipe)
        //   - server: net.createServer(sock => handleConnection(db, 'pipe', sock))
        //             .listen(pipePath, ...)
        //   - ACL: default security descriptor restricted to creator +
        //          Administrators; no Everyone ACE (contracts/transport.md).
        //   - collision: on EADDRINUSE emit a [bridge] error naming the pipe
        //                and exit with code 5 so launcher maps it to FR-026.
        //   - start log: [bridge] start transport=pipe listen=\\.\pipe\<name>
        //                       data_dir=<abs>
        console.error('[bridge] transport=pipe is not implemented in this slice');
        process.exit(64);
    }

    if (args.transport !== 'tcp') {
        console.error(`[bridge] unknown transport: ${args.transport}`);
        process.exit(64);
    }

    const server = net.createServer((socket) =>
        handleConnection(db, 'tcp', socket)
    );

    server.on('error', (err) => {
        if (err && err.code === 'EADDRINUSE') {
            console.error(
                `[bridge] tcp port ${args.host}:${args.port} already in use`
            );
            process.exit(5);
        }
        console.error(`[bridge] listen error: ${err && err.message}`);
        process.exit(1);
    });

    server.listen(args.port, args.host, () => {
        console.log(
            `[bridge] start transport=tcp ` +
            `listen=${args.host}:${args.port} data_dir=${dataDirAbs}`
        );
    });

    const shutdown = (signal) => {
        server.close(async () => {
            try { await db.close(); } catch (_) { /* ignore */ }
            process.exit(0);
        });
        // Force-exit after 2s if close() hangs.
        setTimeout(() => process.exit(0), 2000).unref();
        void signal;
    };
    process.on('SIGTERM', () => shutdown('SIGTERM'));
    process.on('SIGINT', () => shutdown('SIGINT'));
}

main().catch((err) => {
    console.error(`[bridge] fatal: ${(err && err.stack) || err}`);
    process.exit(1);
});
