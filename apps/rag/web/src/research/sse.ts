/**
 * Lector incremental de `text/event-stream` (WHATWG, «Server-sent events»). `EventSource`
 * solo hace GET, y `/answers` es un POST: el cuerpo llega por `fetch` en trozos que pueden
 * cortar una línea, un evento o un carácter de varios bytes en cualquier punto.
 */
export interface ServerSentEvent {
  readonly event: string;
  readonly data: string;
  readonly id: string | null;
}

const DEFAULT_EVENT = "message";
const LINE_FEED = "\n";
const CARRIAGE_RETURN = "\r";
const FIELD_SEPARATOR = ":";
const BYTE_ORDER_MARK = "\uFEFF";

export class SseParser {
  private buffer = "";
  private eventName = DEFAULT_EVENT;
  private dataLines: string[] = [];
  private lastId: string | null = null;
  private started = false;

  /** Los eventos completos que cierra `chunk`; lo que queda a medias espera el siguiente. */
  push(chunk: string): ServerSentEvent[] {
    this.buffer += this.started ? chunk : this.withoutByteOrderMark(chunk);
    const events: ServerSentEvent[] = [];
    let lineStart = 0;
    for (let index = 0; index < this.buffer.length; index += 1) {
      const character = this.buffer.charAt(index);
      if (character !== LINE_FEED && character !== CARRIAGE_RETURN) {
        continue;
      }
      // Un `\r` al final puede ser la mitad de un `\r\n`: se espera al siguiente trozo.
      if (character === CARRIAGE_RETURN && index === this.buffer.length - 1) {
        break;
      }
      const event = this.consume(this.buffer.slice(lineStart, index));
      if (character === CARRIAGE_RETURN && this.buffer.charAt(index + 1) === LINE_FEED) {
        index += 1;
      }
      lineStart = index + 1;
      if (event !== null) {
        events.push(event);
      }
    }
    this.buffer = this.buffer.slice(lineStart);
    return events;
  }

  /**
   * Al cerrarse el cuerpo. Un `\r` que quedó esperando era un fin de línea; un evento sin su
   * línea en blanco final se descarta (WHATWG).
   */
  finish(): ServerSentEvent[] {
    const pending = this.buffer;
    this.buffer = "";
    const events: ServerSentEvent[] = [];
    if (pending.endsWith(CARRIAGE_RETURN)) {
      const event = this.consume(pending.slice(0, -1));
      if (event !== null) {
        events.push(event);
      }
    }
    this.reset();
    return events;
  }

  private withoutByteOrderMark(chunk: string): string {
    if (chunk === "") {
      return chunk;
    }
    this.started = true;
    return chunk.startsWith(BYTE_ORDER_MARK) ? chunk.slice(1) : chunk;
  }

  private consume(line: string): ServerSentEvent | null {
    if (line === "") {
      return this.dispatch();
    }
    if (line.startsWith(FIELD_SEPARATOR)) {
      return null;
    }
    const separator = line.indexOf(FIELD_SEPARATOR);
    const field = separator < 0 ? line : line.slice(0, separator);
    const rawValue = separator < 0 ? "" : line.slice(separator + 1);
    const value = rawValue.startsWith(" ") ? rawValue.slice(1) : rawValue;
    if (field === "event") {
      this.eventName = value;
    } else if (field === "data") {
      this.dataLines.push(value);
    } else if (field === "id" && !value.includes("\0")) {
      this.lastId = value;
    }
    return null;
  }

  private dispatch(): ServerSentEvent | null {
    if (this.dataLines.length === 0) {
      this.reset();
      return null;
    }
    const event = { event: this.eventName || DEFAULT_EVENT, data: this.dataLines.join("\n"), id: this.lastId };
    this.reset();
    return event;
  }

  private reset(): void {
    this.eventName = DEFAULT_EVENT;
    this.dataLines = [];
  }
}
