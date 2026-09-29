import {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
} from "react";
import type { ReactNode, ButtonHTMLAttributes } from "react";
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
  const [data, setData] = useState<T>(initial);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const reload = useCallback(async () => {
    if (!path) {
      setLoading(false);
      return;
    }
    try {
      const d = await api<T>(path);
      setData(d);
      setError("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [path]);
  useEffect(() => {
    setLoading(true);
    void reload();
  }, [reload]);
  return { data, setData, error, loading, reload };
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
export function Tabs({
  items,
  value,
  onChange,
}: {
  items: { id: string; label: string }[];
  value: string;
  onChange: (s: string) => void;
}) {
  return (
    <div className="tabs" role="tablist">
      {items.map((item) => (
        <button
          key={item.id}
          role="tab"
          aria-selected={value === item.id}
          className={value === item.id ? "active" : ""}
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
