import net from "node:net";

/**
 * A socket the server has already dropped must fail its request, not the whole app.
 *
 * Node 24's built-in fetch (undici 7.28, as Electron 43 ships it) calls `socket.setTypeOfService(0)`
 * on every HTTP/1 request, unguarded, inside a socket callback. On macOS the underlying
 * `setsockopt` fails with EINVAL once the peer has closed the connection, so a request on a socket
 * the server dropped throws out of an event handler — an uncaught exception, which Electron shows
 * as a modal "A JavaScript error occurred in the main process" dialog. That is what the tray's
 * runaway reads produced at 222 open sockets (see `window/tray.ts`, `singleFlight`), and what any
 * main-process fetch would produce if the server restarted under it.
 *
 * Zero is the default type of service, so failing to set it changes nothing about the socket; the
 * request that follows fails through undici's own error path, which every caller here already
 * handles. libuv makes the same allowance for the sibling options (`setNoDelay`, keep-alive) on
 * macOS. Only EINVAL is swallowed — anything else is still thrown.
 */
type MaybeTos = { setTypeOfService?: (tos: number) => unknown };

export function tolerateDroppedSockets(prototype: object = net.Socket.prototype): void {
  // Typed loosely on purpose: the installed @types/node predates `setTypeOfService`, which is
  // exactly why nothing here noticed the call being added underneath it.
  const target = prototype as MaybeTos;
  const original = target.setTypeOfService;
  if (typeof original !== "function" || (original as { tolerant?: boolean }).tolerant) return;
  const tolerant = function (this: unknown, tos: number) {
    try {
      return original.call(this, tos);
    } catch (error) {
      if ((error as NodeJS.ErrnoException | null)?.code === "EINVAL") return this;
      throw error;
    }
  };
  Object.assign(tolerant, { tolerant: true });
  target.setTypeOfService = tolerant;
}
