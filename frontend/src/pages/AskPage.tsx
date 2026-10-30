import DOMPurify from "dompurify";
import { marked } from "marked";
import { useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, askStream, type AskEvent } from "../api";
import { ApiKeyPanel, loadKey } from "../components/ApiKeyPanel";
import { useAsync } from "../lib";

interface Activity {
  id: string;
  name: string;
  input: Record<string, unknown>;
  summary?: string;
  is_error?: boolean;
}

interface Turn {
  question: string;
  answer: string;
  activity: Activity[];
  notices: string[];
  error?: string;
  done: boolean;
  model?: string;
}

const EXAMPLES = [
  "Which families changed order between APG III and APG IV?",
  "What are the possible apomorphies of Vitaceae, and how old is the family?",
  "Where does APWeb depart from APG IV in the placement of families?",
  "Which parts of the tree do APWeb's own pages disagree about?",
];

const MODEL_LABELS: Record<string, string> = {
  "claude-opus-5-5": "Opus 5.5 · best",
  "claude-sonnet-5-5": "Sonnet 5.5 · ½ cost",
};

const VERBS: Record<string, string> = {
  get_taxon: "Looked up",
  get_subtree: "Opened the tree below",
  get_literature: "Read the literature for",
  search: "Searched for",
  find_references: "Searched the bibliography for",
  get_reference: "Read reference",
  compare_classifications: "Compared",
  clade_agreement: "Compared clades of",
  list_conflicts: "Listed placement conflicts",
  list_sources: "Listed sources",
};

function describe(a: Activity): string {
  const i = a.input;
  const what =
    (i.name as string) ??
    (i.query as string) ??
    (i.a && i.b ? `${i.a} and ${i.b}` : undefined) ??
    (i.reference_id !== undefined ? `#${i.reference_id}` : "");
  const topic = i.topic ? ` (${i.topic})` : "";
  const src = i.source ? ` in ${i.source}` : "";
  return `${VERBS[a.name] ?? a.name} ${what ?? ""}${topic}${src}`.trim();
}

function Markdown({ text }: { text: string }) {
  const nav = useNavigate();
  const html = useMemo(
    () => DOMPurify.sanitize(marked.parse(text, { async: false, gfm: true }) as string),
    [text],
  );
  // route internal links (/taxon/…, /reference/…) through the router
  const onClick = (e: MouseEvent<HTMLDivElement>) => {
    const a = (e.target as HTMLElement).closest("a");
    const href = a?.getAttribute("href");
    if (a && href?.startsWith("/") && !e.metaKey && !e.ctrlKey) {
      e.preventDefault();
      nav(href);
    } else if (a && href && !href.startsWith("/")) {
      a.setAttribute("target", "_blank");
      a.setAttribute("rel", "noreferrer");
    }
  };
  return <div className="markdown" onClick={onClick} dangerouslySetInnerHTML={{ __html: html }} />;
}

export function AskPage() {
  const status = useAsync(() => api.askStatus(), []);
  const [params, setParams] = useSearchParams();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [conversation, setConversation] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [apiKey, setApiKey] = useState<string | null>(loadKey());
  const [keyProblem, setKeyProblem] = useState<string | null>(null);
  const [model, setModel] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  const st = status.data;
  const canAsk = !!st?.enabled && (!!apiKey || !!st?.server_key);

  const update = (fn: (t: Turn) => Turn) =>
    setTurns((ts) => [...ts.slice(0, -1), fn(ts[ts.length - 1])]);

  async function submit(question: string) {
    question = question.trim();
    if (!question || busy) return;
    setDraft("");
    setBusy(true);
    setTurns((ts) => [...ts, { question, answer: "", activity: [], notices: [], done: false }]);
    const ctrl = new AbortController();
    abort.current = ctrl;
    try {
      await askStream(question, { conversationId: conversation, apiKey, model }, ctrl.signal, (e: AskEvent) => {
        switch (e.type) {
          case "conversation":
            setConversation(e.id);
            setModel(e.model);
            break;
          case "text":
            update((t) => ({ ...t, answer: t.answer + e.text }));
            break;
          case "tool_call":
            update((t) => ({ ...t, activity: [...t.activity, { id: e.id, name: e.name, input: e.input }] }));
            break;
          case "tool_result":
            update((t) => ({
              ...t,
              activity: t.activity.map((a) => (a.id === e.id ? { ...a, summary: e.summary, is_error: e.is_error } : a)),
            }));
            break;
          case "notice":
            update((t) => ({ ...t, notices: [...t.notices, e.text] }));
            break;
          case "done":
            update((t) => ({ ...t, done: true, model: e.model }));
            break;
          case "error":
            update((t) => ({ ...t, error: e.text, done: true }));
            if (e.auth) setKeyProblem(e.text);
            break;
        }
      });
    } catch (err) {
      if ((err as Error).name !== "AbortError") update((t) => ({ ...t, error: (err as Error).message, done: true }));
      else update((t) => ({ ...t, error: "Stopped.", done: true }));
    } finally {
      setBusy(false);
      abort.current = null;
    }
  }

  // ?q=… (from the home page) asks immediately
  useEffect(() => {
    const q = params.get("q");
    if (q && canAsk && !turns.length) {
      setParams({}, { replace: true });
      submit(q);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status.data, apiKey]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns.length, busy]);

  const enabled = canAsk;

  return (
    <div className="ask">
      <div className="ask-head">
        <h1>Ask</h1>
        {turns.length > 0 && !busy && (
          <button
            className="button secondary small"
            onClick={() => {
              setTurns([]);
              setConversation(null);
              setModel(null);
            }}
          >
            New conversation
          </button>
        )}
      </div>
      {st && !st.enabled && (
        <div className="note">
          Question answering is turned off on this server; everything else in the explorer works without it.
        </div>
      )}
      {st?.enabled && st.accepts_user_key && (
        <ApiKeyPanel
          apiKey={apiKey}
          optional={st.server_key}
          problem={keyProblem}
          onChange={(k) => {
            setApiKey(k);
            setKeyProblem(null);
            // a conversation belongs to the key that started it
            setConversation(null);
            setTurns([]);
          }}
        />
      )}
      {st?.enabled && !st.accepts_user_key && !st.server_key && (
        <div className="note">
          Question answering needs an Anthropic API key in the server's environment (<code>ANTHROPIC_API_KEY</code>).
        </div>
      )}
      {turns.length === 0 && (
        <div className="ask-intro">
          <p className="lede small">
            Ask about relationships, classification changes, characters or literature. Claude answers by querying
            the explorer's data (APWeb and APG I–IV) and links each taxon and reference it uses, so you can check
            the source.
          </p>
          <div className="examples">
            {EXAMPLES.map((q) => (
              <button key={q} className="example" disabled={!enabled} onClick={() => submit(q)}>
                {q}
              </button>
            ))}
          </div>
        </div>
      )}

      {turns.map((t, i) => (
        <section key={i} className="turn">
          <p className="question">{t.question}</p>
          {t.activity.length > 0 && (
            <details className="activity" open={!t.done}>
              <summary>
                {t.done ? `Consulted the data ${t.activity.length} time${t.activity.length > 1 ? "s" : ""}` : "Working…"}
              </summary>
              <ul>
                {t.activity.map((a) => (
                  <li key={a.id} className={a.is_error ? "failed" : undefined}>
                    {describe(a)}
                    {a.summary ? <span className="muted"> → {a.summary}</span> : <span className="muted"> …</span>}
                  </li>
                ))}
              </ul>
            </details>
          )}
          {t.notices.map((n, j) => (
            <p key={j} className="muted small">
              {n}
            </p>
          ))}
          {t.answer ? <Markdown text={t.answer} /> : !t.done && <p className="loading">Thinking…</p>}
          {t.error && <div className="error">{t.error}</div>}
          {t.done && !t.error && t.model && <p className="muted small">Answered by {t.model} from the explorer's data.</p>}
        </section>
      ))}
      <div ref={bottom} />

      <form
        className="ask-form"
        onSubmit={(e) => {
          e.preventDefault();
          submit(draft);
        }}
      >
        <textarea
          value={draft}
          rows={2}
          placeholder={turns.length ? "Ask a follow-up…" : "Ask a question about angiosperm phylogeny…"}
          disabled={!enabled}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit(draft);
            }
          }}
        />
        {st && st.models.length > 1 && (
          <select
            aria-label="Model"
            title={conversation ? "Start a new conversation to change the model" : "Model"}
            value={model ?? st.model}
            disabled={!!conversation || busy}
            onChange={(e) => setModel(e.target.value)}
          >
            {st.models.map((m) => (
              <option key={m} value={m}>
                {MODEL_LABELS[m] ?? m}
              </option>
            ))}
          </select>
        )}
        {busy ? (
          <button type="button" className="button secondary" onClick={() => abort.current?.abort()}>
            Stop
          </button>
        ) : (
          <button type="submit" className="button" disabled={!enabled || !draft.trim()}>
            Ask
          </button>
        )}
      </form>
    </div>
  );
}
