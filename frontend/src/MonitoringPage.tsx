"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import {
  Activity, AlertTriangle, ArrowUpRight, Boxes, BrainCircuit, CheckCircle2,
  CircleAlert, Clock3, Gauge, LayoutDashboard, LockKeyhole, RefreshCw,
  Settings, ShieldCheck, TestTube2, Timer, Workflow,
} from "lucide-react";

const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

type ApplicationOption = { id: string; name: string };
type MonitoringData = {
  generated_at: string;
  period: string;
  applications: ApplicationOption[];
  summary: {
    test_pass_rate: number | null;
    passed_tests: number;
    failed_tests: number;
    skipped_tests: number;
    active_runs: number;
    critical_defects: number;
    open_high_defects: number;
    quality_gate_passed: number;
    quality_gate_total: number;
    latest_gate_decision: string | null;
    human_review_decisions: number;
  };
  execution_trend: Array<{ date: string; passed: number; failed: number }>;
  test_categories: Array<{ category: string; count: number }>;
  recent_runs: Array<{ id: string; status: string; created_at: string; suite__name: string }>;
  recent_reviews: Array<{ id: string; decision: string; created_at: string; run__suite__name: string }>;
  integrations: Record<string, { configured: boolean; provider?: string; [key: string]: string | number | boolean | null | undefined }>;
  dora_metrics: Record<string, unknown> | null;
};

const navItems = [
  { label: "Overview", href: "/", icon: LayoutDashboard },
  { label: "Applications", href: "/", icon: Boxes },
  { label: "Test Execution", href: "/", icon: TestTube2 },
  { label: "Monitoring", href: "/monitoring", icon: Activity },
  { label: "Quality Gates", href: "/monitoring#quality-gates", icon: ShieldCheck },
  { label: "Defects", href: "/monitoring#defects", icon: CircleAlert },
  { label: "Administration", href: "/", icon: Settings },
];

function MetricCard({ label, value, note, icon: Icon, tone = "blue" }: {
  label: string; value: string; note: string; icon: typeof Activity; tone?: "blue" | "green" | "red" | "amber";
}) {
  return <article className="monitor-card">
    <div className={`metric-icon ${tone}`}><Icon size={18}/></div>
    <p>{label}</p>
    <strong>{value}</strong>
    <span>{note}</span>
  </article>;
}

function IntegrationCard({ title, description, configured, provider, icon: Icon }: {
  title: string; description: string; configured: boolean; provider?: string; icon: typeof Activity;
}) {
  return <article className="integration-card">
    <div className="integration-icon"><Icon size={18}/></div>
    <div className="integration-copy"><h3>{title}</h3><p>{description}</p><small>{provider ?? "Integration"} · {configured ? "Connected" : "Not configured"}</small></div>
    <span className={configured ? "connection connected" : "connection"}>{configured ? "Live" : "Pending"}</span>
  </article>;
}

function TrendChart({ points }: { points: MonitoringData["execution_trend"] }) {
  const width = 660;
  const height = 190;
  const max = Math.max(1, ...points.flatMap((point) => [point.passed, point.failed]));
  const getPoints = (key: "passed" | "failed") => points.map((point, index) => {
    const x = points.length < 2 ? width / 2 : 18 + index * (width - 36) / (points.length - 1);
    const y = height - 20 - (point[key] / max) * (height - 44);
    return `${x},${y}`;
  }).join(" ");

  if (!points.length) return <div className="chart-empty">No test execution history for this period.</div>;
  return <div className="chart-wrap">
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Passed and failed test execution trend">
      {[0, 1, 2, 3].map((line) => <line key={line} x1="18" x2={width - 18} y1={24 + line * 46} y2={24 + line * 46} className="chart-gridline"/>)}
      {points.length > 1 && <><polyline points={getPoints("passed")} className="trend-line pass-line"/><polyline points={getPoints("failed")} className="trend-line fail-line"/></>}
      {points.map((point, index) => {
        const x = points.length < 2 ? width / 2 : 18 + index * (width - 36) / (points.length - 1);
        const y = (value: number) => height - 20 - (value / max) * (height - 44);
        return <g key={point.date}><circle cx={x} cy={y(point.passed)} r="4" className="pass-dot"/><circle cx={x} cy={y(point.failed)} r="4" className="fail-dot"/></g>;
      })}
    </svg>
    <div className="chart-labels"><span>{points[0].date}</span><span>{points[points.length - 1].date}</span></div>
    <div className="chart-legend"><span><i className="legend-dot pass-dot"/>Passed</span><span><i className="legend-dot fail-dot"/>Failed</span></div>
  </div>;
}

export default function MonitoringPage() {
  const [token, setToken] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [loginError, setLoginError] = useState("");
  const [data, setData] = useState<MonitoringData | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [applicationId, setApplicationId] = useState("");
  const [period, setPeriod] = useState("24h");
  const [section, setSection] = useState<"qa" | "infrastructure">("qa");
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    setToken(window.sessionStorage.getItem("zimworx_access_token") ?? "");
  }, []);

  useEffect(() => {
    if (!token) return;
    const params = new URLSearchParams({ period });
    if (applicationId) params.set("application", applicationId);
    setLoading(true);
    setError("");
    fetch(`${API_URL}/api/monitoring/?${params.toString()}`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then(async (response) => {
        if (response.status === 401) throw new Error("Your session expired. Sign in again to continue.");
        if (!response.ok) {
          const body = await response.json().catch(() => ({}));
          throw new Error(body.detail ?? "Monitoring data could not be loaded.");
        }
        return response.json() as Promise<MonitoringData>;
      })
      .then((result) => {
        setData(result);
        if (applicationId && !result.applications.some((app) => app.id === applicationId)) setApplicationId("");
      })
      .catch((cause: Error) => setError(cause.message))
      .finally(() => setLoading(false));
  }, [token, applicationId, period, refreshKey]);

  const summary = data?.summary;
  const passedRuns = useMemo(() => data?.execution_trend.reduce((total, point) => total + point.passed, 0) ?? 0, [data]);

  async function signIn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoginError("");
    try {
      const response = await fetch(`${API_URL}/api/auth/token/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      if (!response.ok) throw new Error("Sign-in failed. Check your username and password.");
      const result = await response.json();
      window.sessionStorage.setItem("zimworx_access_token", result.access);
      setToken(result.access);
      setPassword("");
    } catch (cause) {
      setLoginError(cause instanceof Error ? cause.message : "Unable to sign in.");
    }
  }

  function signOut() {
    window.sessionStorage.removeItem("zimworx_access_token");
    setToken("");
    setData(null);
  }

  return <main className="shell">
    <aside className="sidebar">
      <a className="brand" href="/"><Gauge size={22}/><strong>Zimworx QA</strong></a>
      <nav>{navItems.map(({ label, href, icon: Icon }) =>
        <a key={label} href={href} className={label === "Monitoring" ? "nav active" : "nav"}><Icon size={18}/><span>{label}</span></a>
      )}</nav>
      <div className="sidebar-foot"><span className="live-indicator"/>QA monitoring</div>
    </aside>

    <section className="content">
      <header className="topbar monitor-topbar">
        <div><p className="eyebrow">Engineering intelligence / monitoring</p><h1>Zimworx QA Monitoring</h1></div>
        <div className="topbar-actions">
          {token && <button className="subtle-button" onClick={signOut}>Sign out</button>}
          <button className="icon-button" aria-label="Refresh monitoring data" disabled={!token || loading} onClick={() => setRefreshKey((key) => key + 1)}><RefreshCw size={17}/></button>
        </div>
      </header>

      <div className="monitor-content">
        <div className="monitor-toolbar">
          <div className="segmented-control" aria-label="Monitoring section">
            <button className={section === "qa" ? "selected" : ""} onClick={() => setSection("qa")}>QA Health</button>
            <button className={section === "infrastructure" ? "selected" : ""} onClick={() => setSection("infrastructure")}>Infrastructure</button>
          </div>
          <div className="filters">
            <label>Application
              <select value={applicationId} onChange={(event) => setApplicationId(event.target.value)} disabled={!data}>
                <option value="">All applications</option>
                {data?.applications.map((application) => <option key={application.id} value={application.id}>{application.name}</option>)}
              </select>
            </label>
            <label>Time range
              <select value={period} onChange={(event) => setPeriod(event.target.value)}>
                <option value="24h">Last 24 hours</option><option value="7d">Last 7 days</option><option value="30d">Last 30 days</option>
              </select>
            </label>
          </div>
        </div>

        {!token ? <section className="auth-card">
          <div className="metric-icon blue"><LockKeyhole size={20}/></div>
          <h2>Sign in to view monitoring</h2>
          <p>Monitoring data is scoped to your organization. Sign in with your Zimworx QA account.</p>
          <form onSubmit={signIn}>
            <label>Username<input autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} required/></label>
            <label>Password<input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required/></label>
            {loginError && <p className="form-error" role="alert">{loginError}</p>}
            <button className="primary-button" type="submit">Sign in</button>
          </form>
        </section> : error ? <div className="notice" role="alert">{error}</div> : loading && !data ? <div className="loading-state">Loading monitoring data…</div> : section === "qa" ? <>
          <div className="monitor-intro">
            <div><h2>Quick QA Health</h2><p>Live results from registered applications and recorded test runs.</p></div>
            <span className="data-stamp">{data ? `Updated ${new Date(data.generated_at).toLocaleTimeString()}` : "Loading…"}</span>
          </div>

          {data && data.applications.length === 0 ? <div className="empty-panel">No active applications are registered for your organization yet.</div> : <>
            <div className="monitor-grid">
              <MetricCard label="Test pass rate" value={summary?.test_pass_rate == null ? "—" : `${summary.test_pass_rate}%`} note={summary?.test_pass_rate == null ? "No execution data" : `${summary.passed_tests} passed in selected period`} icon={CheckCircle2} tone="green"/>
              <MetricCard label="Failed tests" value={String(summary?.failed_tests ?? "—")} note="Recorded test executions" icon={CircleAlert} tone="red"/>
              <MetricCard label="Critical defects" value={String(summary?.critical_defects ?? "—")} note="Open defects" icon={AlertTriangle} tone="red"/>
              <MetricCard label="Active test runs" value={String(summary?.active_runs ?? "—")} note="Queued or running" icon={Activity}/>
              <MetricCard label="API p95 latency" value="N/A" note="API telemetry not connected" icon={Timer} tone="amber"/>
              <MetricCard label="Skipped tests" value={String(summary?.skipped_tests ?? "—")} note="Recorded test executions" icon={TestTube2}/>
              <MetricCard label="Quality gates" value={`${summary?.quality_gate_passed ?? "—"} / ${summary?.quality_gate_total ?? "—"}`} note={summary?.latest_gate_decision ? `Latest: ${summary.latest_gate_decision}` : "No gate decisions recorded"} icon={ShieldCheck} tone={summary?.latest_gate_decision === "PASS" ? "green" : "amber"}/>
              <MetricCard label="Open high defects" value={String(summary?.open_high_defects ?? "—")} note="Unresolved high severity" icon={AlertTriangle} tone="amber"/>
            </div>

            <div className="monitor-panels">
              <section className="monitor-panel trend-panel">
                <div className="section-heading"><div><h2>Test execution trend</h2><p>{passedRuns} passed executions in visible history</p></div><span className="panel-period">{period}</span></div>
                <TrendChart points={data?.execution_trend ?? []}/>
              </section>
              <section className="monitor-panel">
                <div className="section-heading"><div><h2>Test coverage by engine</h2><p>Categories represented in recorded executions</p></div></div>
                {data?.test_categories.length ? <div className="category-list">{data.test_categories.map((item) => <div className="category-row" key={item.category}><span>{item.category || "Uncategorized"}</span><strong>{item.count}</strong></div>)}</div> : <div className="chart-empty">No categorized test executions recorded.</div>}
              </section>
            </div>

            <section className="monitor-panel api-telemetry-panel">
              <div className="section-heading"><div><h2>API monitoring</h2><p>Latency and request metrics are unavailable until an API telemetry source is connected.</p></div><span className="connection">HTTPX / Prometheus · Pending</span></div>
              <div className="api-metric-row">
                {[["p50 latency", "N/A"], ["p95 latency", "N/A"], ["p99 latency", "N/A"], ["HTTP 4xx / 5xx", "N/A"], ["Requests / second", "N/A"]].map(([label, value]) => <div key={label}><span>{label}</span><strong>{value}</strong></div>)}
              </div>
            </section>

            <section className="monitor-panel integrations-panel">
              <div className="section-heading"><div><h2>QA systems and integrations</h2><p>Integration states are reported as pending until connected and producing data.</p></div></div>
              <div className="integration-grid">
                <IntegrationCard title="HTTPX API monitoring" description="Latency percentiles, HTTP errors and request rate" configured={data?.integrations.api_latency.configured ?? false} provider="Prometheus / HTTPX" icon={Activity}/>
                <IntegrationCard title="Playwright browser tests" description="Runs, screenshots and browser console errors" configured={data?.integrations.browser.configured ?? false} provider="Playwright" icon={TestTube2}/>
                <IntegrationCard title="Locust performance" description="Virtual users, throughput and error rate" configured={data?.integrations.performance.configured ?? false} provider="Locust" icon={Workflow}/>
                <IntegrationCard title="OWASP ZAP security" description="Scan history and severity-ranked findings" configured={data?.integrations.security.configured ?? false} provider="OWASP ZAP" icon={LockKeyhole}/>
                <IntegrationCard title="CI/CD quality gates" description="Build, integration and staging release status" configured={data?.integrations.ci_cd.configured ?? false} provider="GitHub Actions" icon={ShieldCheck}/>
                <IntegrationCard title="DORA analytics" description="Deployment frequency, lead time, CFR and recovery" configured={data?.dora_metrics !== null && data?.dora_metrics !== undefined} provider="Deployment and incident records" icon={ArrowUpRight}/>
                <IntegrationCard title="AI QA assistant" description="Human-reviewed failure analysis and test proposals" configured={data?.integrations.ai_assistant.configured ?? false} provider="AI service" icon={BrainCircuit}/>
              </div>
            </section>

            <div className="monitor-panels">
              <section className="monitor-panel" id="quality-gates">
                <div className="section-heading"><div><h2>Release readiness</h2><p>Based only on recorded quality gate decisions.</p></div></div>
                <div className="readiness-state"><ShieldCheck size={22}/><div><strong>{summary?.latest_gate_decision ?? "Not evaluated"}</strong><span>{summary?.latest_gate_decision ? "Latest recorded gate decision" : "No gate evidence is recorded for this selection."}</span></div></div>
              </section>
              <section className="monitor-panel" id="defects">
                <div className="section-heading"><div><h2>Recent test runs</h2><p>Run data from the selected applications and time range.</p></div></div>
                {data?.recent_runs.length ? <div className="run-list">{data.recent_runs.map((run) => <div className="run-row" key={run.id}><span className={`run-state ${run.status}`}/><div><strong>{run.suite__name || "Test suite"}</strong><small>{new Date(run.created_at).toLocaleString()}</small></div><span className="run-status">{run.status}</span></div>)}</div> : <div className="chart-empty">No test runs recorded for this period.</div>}
              </section>
            </div>

            <section className="monitor-panel human-review-panel">
              <div className="section-heading"><div><h2>Human QA review</h2><p>{summary?.human_review_decisions ?? 0} decisions recorded in this period. Human review remains the release authority.</p></div></div>
              {data?.recent_reviews.length ? <div className="run-list">{data.recent_reviews.map((review) => <div className="run-row" key={review.id}><span className="run-state completed"/><div><strong>{review.run__suite__name || "Test suite"} · {review.decision}</strong><small>{new Date(review.created_at).toLocaleString()}</small></div></div>)}</div> : <div className="chart-empty">No human review decisions recorded for this period.</div>}
            </section>

            <section className="action-strip">
              <div className="action-mark"><Clock3 size={19}/></div>
              <div><strong>Test execution controls</strong><p>Run Tests is unavailable until an approved suite is connected to an execution worker. No test is started from this dashboard yet.</p></div>
              <button className="disabled-button" disabled title="Execution worker is not connected">Run Tests</button>
            </section>
          </>}
        </> : <section className="monitor-panel infra-empty">
          <div className="metric-icon blue"><Activity size={20}/></div>
          <h2>Infrastructure metrics are not connected</h2>
          <p>Prometheus is present in the deployment, but Node Exporter and infrastructure time-series have not been configured for this dashboard. CPU, memory, disk, network and uptime will appear here once metrics are available.</p>
          <div className="infra-status"><span className="connection">Node Exporter · Pending</span><span className="connection">Host metrics · No data</span></div>
        </section>}

        {data?.dora_metrics && <section className="monitor-panel dora-panel"><div className="section-heading"><div><h2>DORA metrics</h2><p>Values from the latest stored metric snapshot.</p></div></div><pre>{JSON.stringify(data.dora_metrics, null, 2)}</pre></section>}
      </div>
    </section>
  </main>;
}
