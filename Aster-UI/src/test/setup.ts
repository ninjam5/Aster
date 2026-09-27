import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// jsdom doesn't implement scrollTo/scrollIntoView; framer-motion's height:"auto"
// measurement and our own autoscroll-to-bottom (Activity Log) call these, which
// otherwise spams "Not implemented" errors to stderr during tests.
window.scrollTo = () => {};
Element.prototype.scrollTo = () => {};
Element.prototype.scrollIntoView = () => {};

// jsdom has no canvas 2D context (would need the native `canvas` package) and
// logs a "Not implemented" error per call. PixelAsterCharacter guards on a
// null context and skips its rAF loop, so returning null quietly is correct.
HTMLCanvasElement.prototype.getContext = (() => null) as typeof HTMLCanvasElement.prototype.getContext;

// jsdom's real WebSocket actually dials 127.0.0.1 and fires async close/error
// events mid-test (ActivityLog, AsterMoodProvider). Components under test only
// need the constructor + close(); suites that care stub their own.
class InertWebSocket {
  onopen: ((ev?: unknown) => void) | null = null;
  onmessage: ((ev?: unknown) => void) | null = null;
  onclose: ((ev?: unknown) => void) | null = null;
  onerror: ((ev?: unknown) => void) | null = null;
  close() {}
}
globalThis.WebSocket = InertWebSocket as unknown as typeof WebSocket;

afterEach(() => {
  cleanup();
  localStorage.clear();
});
