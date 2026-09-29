import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./styles.css";

class PageBoundary extends React.Component<React.PropsWithChildren, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    if (this.state.failed) return <main className="page"><h1>Page unavailable</h1><p>Reload to open the current version.</p><button onClick={() => window.location.reload()}>Reload page</button></main>;
    return this.props.children;
  }
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <PageBoundary><BrowserRouter>
      <App />
    </BrowserRouter></PageBoundary>
  </React.StrictMode>,
);
