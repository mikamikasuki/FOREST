import {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
  useRef,
} from "react";
import type {
  ReactNode,
  ButtonHTMLAttributes,
  SetStateAction,
  KeyboardEvent as ReactKeyboardEvent,
} from "react";
import {
  AlertCircle,
  Check,
  ChevronRight,
  LoaderCircle,
  Sprout,
  X,
} from "lucide-react";
import { api } from "./api";
export type UIContextType = {
  lang: string;
  theme: string;
  t: (zh: string, en: string) => string;
  notify: (s: string) => void;
  action: <T>(f: () => Promise<T>, message?: string) => Promise<T | undefined>;
};
export const UIContext = createContext<UIContextType>(null!);
export const useUI = () => useContext(UIContext);
export function useLoad<T>(path: string | null, initial: T) {
  const [state, setState] = useState({
    path,
    data: initial,
    error: "",
    loading: true,
  });
  const currentPath = useRef(path);
  const generation = useRef(0);
  const inFlight = useRef<{
    path: string | null;
    pending: boolean;
    promise: Promise<void>;
  } | null>(null);
  currentPath.current = path;
  const reload = useCallback((): Promise<void> => {
    if (!path) {
      ++generation.current;
      setState((old) => ({ ...old, path, loading: false }));
      return Promise.resolve();
    }
    if (inFlight.current?.path === path) {
      inFlight.current.pending = true;
      return inFlight.current.promise;
    }
    const active = { path, pending: false, promise: Promise.resolve() };
    inFlight.current = active;
    active.promise = (async () => {
      do {
        active.pending = false;
        const request = ++generation.current;
        try {
          const data = await api<T>(path);
          if (request === generation.current && currentPath.current === path)
            setState({ path, data, error: "", loading: false });
        } catch (e) {
          if (request === generation.current && currentPath.current === path)
            setState((old) => ({
              path,
              data: old.path === path ? old.data : initial,
              error: (e as Error).message,
              loading: false,
            }));
        }
      } while (active.pending && currentPath.current === path);
      if (inFlight.current === active) inFlight.current = null;
    })();
    return active.promise;
  }, [path]);
  useEffect(() => {
    currentPath.current = path;
    void reload();
    return () => {
      ++generation.current;
      if (currentPath.current === path) currentPath.current = null;
    };
  }, [reload]);
  const setData = useCallback(
    (value: SetStateAction<T>) => {
      ++generation.current;
      if (inFlight.current?.path === path) inFlight.current.pending = true;
      setState((old) => ({
        ...old,
        path,
        data:
          typeof value === "function"
            ? (value as (previous: T) => T)(
                old.path === path ? old.data : initial,
              )
            : value,
      }));
    },
    [path],
  );
  return {
    data: state.path === path ? state.data : initial,
    setData,
    error: state.path === path ? state.error : "",
    loading: state.path !== path || state.loading,
    reload,
  };
}
export function Button({
  children,
  className = "",
  busy,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { busy?: boolean }) {
  return (
    <button
      {...props}
      className={`button ${className}`}
      disabled={busy || props.disabled}
    >
      {busy ? <LoaderCircle size={15} className="spin" /> : null}
      {children}
    </button>
  );
}
export function IconButton({
  label,
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { label: string }) {
  return (
    <button className="icon-button" title={label} aria-label={label} {...props}>
      {children}
    </button>
  );
}
export function Badge({
  status,
  children,
}: {
  status?: string;
  children?: ReactNode;
}) {
  return (
    <span className={`badge status-${status || "idle"}`}>
      <span className="status-dot" />
      {children || status || "idle"}
    </span>
  );
}
export function Empty({
  title,
  description,
  action,
  icon,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-icon">
        {icon || <Sprout size={28} strokeWidth={1.3} />}
      </div>
      <h3>{title}</h3>
      {description && <p>{description}</p>}
      {action}
    </div>
  );
}
export function ErrorBox({
  error,
  retry,
}: {
  error: string;
  retry?: () => void;
}) {
  if (!error) return null;
  return (
    <div className="error-box">
      <AlertCircle size={17} />
      <span>{error}</span>
      {retry && <Button onClick={retry}>Retry</Button>}
    </div>
  );
}
export function Loading() {
  return (
    <div className="loading">
      <LoaderCircle size={22} className="spin" /> Loading workspace…
    </div>
  );
}
export function PageHeading({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="page-heading">
      <div>
        {eyebrow && <div className="eyebrow">{eyebrow}</div>}
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      <div className="heading-actions">{actions}</div>
    </div>
  );
}
export function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  );
}
export function Modal({
  title,
  children,
  onClose,
  wide = false,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
}) {
  useEffect(() => {
    const handle = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handle);
    return () => window.removeEventListener("keydown", handle);
  }, [onClose]);
  return (
    <div
      className="modal-shade"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <section
        className={`modal ${wide ? "wide" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <header>
          <h2>{title}</h2>
          <IconButton label="Close" onClick={onClose}>
            <X size={18} />
          </IconButton>
        </header>
        {children}
      </section>
    </div>
  );
}
export function JsonView({ value }: { value: any }) {
  return <pre className="json-view">{JSON.stringify(value, null, 2)}</pre>;
}
export function tabTargetForKey(
  items: { id: string; label: string }[],
  currentId: string,
  key: string,
) {
  const currentIndex = items.findIndex((item) => item.id === currentId);
  if (currentIndex < 0 || !items.length) return null;
  if (key === "ArrowRight") return items[(currentIndex + 1) % items.length].id;
  if (key === "ArrowLeft")
    return items[(currentIndex - 1 + items.length) % items.length].id;
  if (key === "Home") return items[0].id;
  if (key === "End") return items[items.length - 1].id;
  return null;
}
export function Tabs({
  panelId,
  items,
  value,
  onChange,
}: {
  panelId: string;
  items: { id: string; label: string }[];
  value: string;
  onChange: (s: string) => void;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const onKeyDown = (event: ReactKeyboardEvent<HTMLButtonElement>) => {
    const nextId = tabTargetForKey(
      items,
      event.currentTarget.dataset.tabId || "",
      event.key,
    );
    if (!nextId) return;
    event.preventDefault();
    const nextIndex = items.findIndex((item) => item.id === nextId);
    onChange(nextId);
    refs.current[nextIndex]?.focus();
  };
  return (
    <div className="tabs" role="tablist">
      {items.map((item, index) => (
        <button
          key={item.id}
          ref={(element) => {
            refs.current[index] = element;
          }}
          id={`${panelId}-tab-${item.id}`}
          data-tab-id={item.id}
          type="button"
          role="tab"
          aria-controls={`${panelId}-panel`}
          aria-selected={value === item.id}
          tabIndex={value === item.id ? 0 : -1}
          className={value === item.id ? "active" : ""}
          onKeyDown={onKeyDown}
          onClick={() => onChange(item.id)}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}
export function Breadcrumb({ children }: { children: ReactNode }) {
  return (
    <div className="breadcrumb">
      FOREST <ChevronRight size={12} />
      {children}
    </div>
  );
}
export function Saved() {
  return (
    <span className="saved">
      <Check size={12} /> Saved
    </span>
  );
}
