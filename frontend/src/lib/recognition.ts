// Browser speech recognition (Chrome ships it prefixed): feature-detected, typed minimally.
// D30: this is how /ceo and /talk listen when there is no Realtime session (the main path).

export interface Recognition {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((e: { resultIndex: number; results: ArrayLike<ArrayLike<{ transcript: string }> & { isFinal: boolean }> }) => void) | null;
  onend: (() => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
}
export type RecognitionCtor = new () => Recognition;

export const recognitionCtor = (): RecognitionCtor | null => {
  if (typeof window === "undefined") return null;
  const w = window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
};

/** Errors after which listening can never work on this page (the mic or the service is refused). */
export const isBlockedError = (error: string) => error === "not-allowed" || error === "service-not-allowed";
/** Errors that are just silence or our own abort, not a failing recognizer. */
export const isQuietError = (error: string) => error === "no-speech" || error === "aborted";
/** After this many failures in a row (no result between), stop trying: the caller types. */
export const MAX_EAR_FAILURES = 3;
