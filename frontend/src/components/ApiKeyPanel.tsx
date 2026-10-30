import { useState } from "react";

const SESSION = "apw.anthropicKey"; // sessionStorage: cleared when the tab closes
const DEVICE = "apw.anthropicKey"; // localStorage: only if the visitor opts in

function safe<T>(fn: () => T, fallback: T): T {
  try {
    return fn();
  } catch {
    return fallback;
  }
}

export function loadKey(): string | null {
  return safe(() => sessionStorage.getItem(SESSION) ?? localStorage.getItem(DEVICE), null);
}

function saveKey(key: string, remember: boolean) {
  safe(() => {
    sessionStorage.setItem(SESSION, key);
    if (remember) localStorage.setItem(DEVICE, key);
    else localStorage.removeItem(DEVICE);
  }, undefined);
}

export function forgetKey() {
  safe(() => {
    sessionStorage.removeItem(SESSION);
    localStorage.removeItem(DEVICE);
  }, undefined);
}

const isRemembered = () => safe(() => localStorage.getItem(DEVICE) !== null, false);

export function maskKey(key: string): string {
  return `${key.slice(0, 7)}…${key.slice(-4)}`;
}

interface Props {
  apiKey: string | null;
  onChange: (key: string | null) => void;
  optional: boolean; // the server also has its own key
  problem?: string | null; // e.g. "The Anthropic API rejected the API key."
}

/** Lets a visitor supply their own Anthropic API key for the Ask page. */
export function ApiKeyPanel({ apiKey, onChange, optional, problem }: Props) {
  const [editing, setEditing] = useState(!apiKey && !optional);
  const [draft, setDraft] = useState("");
  const [remember, setRemember] = useState(isRemembered());
  const valid = /^sk-ant-[A-Za-z0-9_-]{20,200}$/.test(draft.trim());

  if (apiKey && !editing && !problem) {
    return (
      <div className="key-panel compact">
        Using your Anthropic API key <code>{maskKey(apiKey)}</code>
        {isRemembered() ? " (remembered on this device)" : " (for this browser tab)"} ·{" "}
        <button className="link" onClick={() => setEditing(true)}>
          change
        </button>{" "}
        ·{" "}
        <button
          className="link"
          onClick={() => {
            forgetKey();
            onChange(null);
            setEditing(!optional);
          }}
        >
          forget
        </button>
      </div>
    );
  }

  if (!editing && !problem) {
    return (
      <div className="key-panel compact">
        Answers use this server's key.{" "}
        <button className="link" onClick={() => setEditing(true)}>
          Use your own Anthropic API key instead
        </button>
      </div>
    );
  }

  return (
    <div className="key-panel">
      {problem && <p className="error">{problem}</p>}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (!valid) return;
          saveKey(draft.trim(), remember);
          onChange(draft.trim());
          setDraft("");
          setEditing(false);
        }}
      >
        <label htmlFor="apikey">
          <strong>Your Anthropic API key</strong>
        </label>
        <div className="key-row">
          <input
            id="apikey"
            type="password"
            autoComplete="off"
            spellCheck={false}
            placeholder="sk-ant-…"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
          />
          <button className="button" type="submit" disabled={!valid}>
            Use key
          </button>
          {(apiKey || optional) && (
            <button type="button" className="button secondary" onClick={() => setEditing(false)}>
              Cancel
            </button>
          )}
        </div>
        <label className="toggle small">
          <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} /> Remember on
          this device (otherwise it's forgotten when you close this tab)
        </label>
      </form>
      <ul className="small muted key-notes">
        <li>
          Create a key in the{" "}
          <a href="https://console.anthropic.com/settings/keys" target="_blank" rel="noreferrer">
            Claude Console
          </a>
          . Questions are billed to your Anthropic API account, which is separate from a Claude.ai subscription.
        </li>
        <li>
          Your key is sent to this server only to make your own requests to Anthropic. The server doesn't store or log
          it, and your conversations can't be continued with a different key.
        </li>
        <li>Each question takes several model calls; choosing Sonnet below costs about half as much as Opus.</li>
      </ul>
    </div>
  );
}
