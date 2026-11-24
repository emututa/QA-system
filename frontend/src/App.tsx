import { FormEvent, useCallback, useEffect, useState } from "react";
import {
  Activity, Boxes, CircleAlert, Gauge, LayoutDashboard, LogOut,
  Plus, RefreshCw, Settings, ShieldCheck, TestTube2,
} from "lucide-react";
import MonitoringPage from "./MonitoringPage";

const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
const TOKEN_KEY = "zimworx_access_token";

type Dashboard = {
  total_applications: number;
  active_runs: number;
  passed_tests: number;
  failed_tests: number;
  critical_defects: number;
  recent_runs: Array<{ id: string; status: string; created_at: string; application?: string }>;
};
type ApplicationItem = {
  id: string;
  name: string;
  description: string;
  github_repository: string;
  default_branch: string;
  staging_url: string;
  status: string;
};
type Profile = {
  username: string;
  is_admin: boolean;
  organization: string | null;
  team: string | null;
  needs_setup: boolean;
};
type Execution = {
  id: string;
  test_name: string;
  status: string;
  error_message: string;
  actual: Record<string, unknown>;
};
type AssessmentRun = {
  id: string;
  status: string;
  created_at: string;
  suite: string;
  executions: Execution[];
};
type ApiError = Error & { data?: Record<string, unknown> };
type Page = "overview" | "applications" | "register";

async function apiRequest<T>(path: string, token: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await fetch(`${API_URL}${path}`, { ...init, headers });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(typeof body.detail === "string" ? body.detail : `Request failed (${response.status}).`) as ApiError;
    error.data = body;
    throw error;
  }
  return body as T;
}

function routeFromPath(path: string): Page {
  if (path === "/applications/new") return "register";
  if (path === "/applications") return "applications";
  return "overview";
}

const wait = (milliseconds: number) => new Promise((resolve) => window.setTimeout(resolve, milliseconds));

export default function App() {
  const [route, setRoute] = useState<Page>(() => routeFromPath(window.location.pathname));
  const [token, setToken] = useState(() => window.sessionStorage.getItem(TOKEN_KEY) ?? "");
  const [profile, setProfile] = useState<Profile | null>(null);
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [applications, setApplications] = useState<ApplicationItem[]>([]);
  const [loginError, setLoginError] = useState("");
  const [error, setError] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [runningApplication, setRunningApplication] = useState("");
  const [runsByApplication, setRunsByApplication] = useState<Record<string, AssessmentRun | undefined>>({});
  const [registrationMessage, setRegistrationMessage] = useState("");
  const [organization, setOrganization] = useState("");
  const [team, setTeam] = useState("");
  const [authorizedTesting, setAuthorizedTesting] = useState(false);
  const [form, setForm] = useState({
    name: "", description: "", github_repository: "", default_branch: "main",
    staging_url: "", technology_stack: "",
  });

  const navigate = useCallback((path: string) => {
    window.history.pushState({}, "", path);
    setRoute(routeFromPath(path));
    setError("");
    setRegistrationMessage("");
  }, []);

  useEffect(() => {
    const onPopState = () => setRoute(routeFromPath(window.location.pathname));
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  const refreshData = useCallback(async (accessToken: string) => {
    setLoading(true);
    setError("");
    try {
      const [currentProfile, dashboardData, applicationData] = await Promise.all([
        apiRequest<Profile>("/api/auth/me/", accessToken),
        apiRequest<Dashboard>("/api/dashboard/", accessToken),
        apiRequest<{ results: ApplicationItem[] } | ApplicationItem[]>("/api/applications/", accessToken),
      ]);
      setProfile(currentProfile);
      setDashboard(dashboardData);
      setApplications(Array.isArray(applicationData) ? applicationData : applicationData.results);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load QA platform data.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (token) void refreshData(token);
  }, [token, refreshData]);

  if (window.location.pathname === "/monitoring") return <MonitoringPage />;

  async function signIn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoginError("");
    try {
      const response = await fetch(`${API_URL}/api/auth/token/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error("Sign-in failed. Check your username and password.");
      window.sessionStorage.setItem(TOKEN_KEY, body.access);
      setPassword("");
      setToken(body.access);
    } catch (cause) {
      setLoginError(cause instanceof Error ? cause.message : "Unable to sign in.");
    }
  }

  function signOut() {
    window.sessionStorage.removeItem(TOKEN_KEY);
    setToken("");
    setProfile(null);
    setDashboard(null);
    setApplications([]);
  }

  async function registerApplication(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading(true);
    setError("");
    setRegistrationMessage("");
    try {
      if (profile?.needs_setup && profile.is_admin) {
        await apiRequest("/api/organizations/bootstrap/", token, {
          method: "POST",
          body: JSON.stringify({ organization, team }),
        });
      }
      const created = await apiRequest<ApplicationItem>("/api/applications/register/", token, {
        method: "POST",
        body: JSON.stringify({
          name: form.name,
          description: form.description,
          github_repository: form.github_repository,
          default_branch: form.default_branch,
          staging_url: form.staging_url,
          authorized_testing: authorizedTesting,
          technology_stack: form.technology_stack.split(",").map((item) => item.trim()).filter(Boolean),
        }),
      });
      setRegistrationMessage(`${created.name} was registered. The source repository is not modified.`);
      setForm({ name: "", description: "", github_repository: "", default_branch: "main", staging_url: "", technology_stack: "" });
      setAuthorizedTesting(false);
      await refreshData(token);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Application registration failed.");
    } finally {
      setLoading(false);
    }
  }

  async function runAssessment(application: ApplicationItem) {
    setRunningApplication(application.id);
    setError("");
    try {
      const queued = await apiRequest<{ id: string; status: string }>(
        `/api/applications/${application.id}/run-assessment/`, token, { method: "POST" },
      );
      setRunsByApplication((current) => ({
        ...current,
        [application.id]: {
          id: queued.id, status: queued.status, created_at: new Date().toISOString(),
          suite: "Repository and staging preflight", executions: [],
        },
      }));
      for (let attempt = 0; attempt < 30; attempt += 1) {
        await wait(1500);
        const runs = await apiRequest<AssessmentRun[]>(
          `/api/applications/${application.id}/assessment-runs/`, token,
        );
        const latest = runs.find((run) => run.id === queued.id);
        if (latest) {
          setRunsByApplication((current) => ({ ...current, [application.id]: latest }));
          if (!["queued", "running"].includes(latest.status)) break;
        }
      }
      await refreshData(token);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not start the QA assessment.");
    } finally {
      setRunningApplication("");
    }
  }

  const pageTitle = route === "register" ? "Register Application" : route === "applications" ? "Applications" : "Overview";
  const navigation = [
    { label: "Overview", path: "/", icon: LayoutDashboard, page: "overview" as const },
    { label: "Applications", path: "/applications", icon: Boxes, page: "applications" as const },
    { label: "Register application", path: "/applications/new", icon: Plus, page: "register" as const },
    { label: "Test Execution", path: "/applications", icon: TestTube2, page: "applications" as const },
    { label: "Monitoring", path: "/monitoring", icon: Activity, page: null },
    { label: "Quality Gates", path: "/applications", icon: ShieldCheck, page: "applications" as const },
    { label: "Defects", path: "/applications", icon: CircleAlert, page: "applications" as const },
    { label: "Administration", path: "/applications", icon: Settings, page: "applications" as const },
  ];

  return <main className="shell">
    <aside className="sidebar">
      <a className="brand" href="/" onClick={(event) => { event.preventDefault(); navigate("/"); }}><Gauge size={22}/><strong>Zimworx QA</strong></a>
      <nav>{navigation.map(({ label, path, icon: Icon, page }) =>
        <a
          key={label}
          href={path}
          onClick={(event) => {
            event.preventDefault();
            if (path === "/monitoring") window.location.href = path;
            else navigate(path);
          }}
          className={page === route ? "nav active" : "nav"}
          aria-current={page === route ? "page" : undefined}
        ><Icon size={18}/><span>{label}</span></a>
      )}</nav>
    </aside>

    <section className="content">
      <header className="topbar">
        <div><p className="eyebrow">Engineering intelligence</p><h1>{pageTitle}</h1></div>
        <div className="topbar-actions">
          {profile?.organization && <span className="environment">{profile.organization}</span>}
          {token
            ? <button className="subtle-button" onClick={signOut}><LogOut size={15}/> Sign out</button>
            : <span className="environment">QA Platform</span>}
          {token && <button className="icon-button" aria-label="Refresh data" disabled={loading} onClick={() => void refreshData(token)}><RefreshCw size={16}/></button>}
        </div>
      </header>

      {error && <div className="notice" role="alert">{error}</div>}
      {!token ? <section className="panel auth-card">
        <h2>Sign in to Zimworx QA</h2>
        <p>Use your QA platform account to register and assess authorized applications.</p>
        <form onSubmit={signIn}>
          <label>Username<input autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} required/></label>
          <label>Password<input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required/></label>
          {loginError && <p className="form-error" role="alert">{loginError}</p>}
          <button className="primary-button" type="submit">Sign in</button>
        </form>
      </section> : route === "register" ? <section className="panel registration-panel">
        <div className="panelHeader"><h2>Connect a repository and staging environment</h2></div>
        <form className="registration-form" onSubmit={registerApplication}>
          {profile?.needs_setup && profile.is_admin && <div className="setup-fields">
            <p>Set up your organization and team before registering the first application.</p>
            <label>Organization name<input value={organization} onChange={(event) => setOrganization(event.target.value)} required maxLength={200} placeholder="Your organization"/></label>
            <label>Team name<input value={team} onChange={(event) => setTeam(event.target.value)} required maxLength={200} placeholder="QA team"/></label>
          </div>}
          {profile?.needs_setup && !profile.is_admin && <div className="notice inline-notice">Your account is not associated with an organization and team yet. Ask your platform administrator to set it up.</div>}
          <div className="form-grid">
            <label>Application name<input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} required maxLength={200} placeholder="RCM Training Platform"/></label>
            <label>GitHub repository URL<input type="url" value={form.github_repository} onChange={(event) => setForm({ ...form, github_repository: event.target.value })} required placeholder="https://github.com/organization/repository"/></label>
            <label>Branch<input value={form.default_branch} onChange={(event) => setForm({ ...form, default_branch: event.target.value })} required maxLength={200} placeholder="main"/></label>
            <label>Staging URL<input type="url" value={form.staging_url} onChange={(event) => setForm({ ...form, staging_url: event.target.value })} required placeholder="https://staging.example.com"/></label>
            <label className="form-wide">Technology stack (optional, comma-separated)<input value={form.technology_stack} onChange={(event) => setForm({ ...form, technology_stack: event.target.value })} placeholder="Detected during repository assessment"/></label>
            <label className="form-wide">Description (optional)<textarea value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} rows={3} placeholder="What does this application do?"/></label>
          </div>
          <div className="scope-note"><ShieldCheck size={17}/><span>The initial QA preflight checks GitHub access and test setup, then sends a read-only GET request to your registered staging URL. It does not modify the repository or execute its code.</span></div>
          <label className="authorization-check"><input type="checkbox" checked={authorizedTesting} onChange={(event) => setAuthorizedTesting(event.target.checked)} required/><span>I am authorized to run these read-only checks against this repository and staging environment.</span></label>
          {registrationMessage && <p className="success-message" role="status">{registrationMessage}</p>}
          <div className="form-actions"><button type="button" className="subtle-button" onClick={() => navigate("/applications")}>Cancel</button><button type="submit" className="primary-button" disabled={loading || Boolean(profile?.needs_setup && !profile.is_admin)}>{loading ? "Registering…" : "Register application"}</button></div>
        </form>
      </section> : route === "applications" ? <div className="workspace">
        <div className="workspace-heading"><div><p className="eyebrow">Connected systems</p><h2>Applications</h2><p>Connect an authorized GitHub repository and staging URL to start a safe QA preflight.</p></div><button className="primary-button" onClick={() => navigate("/applications/new")}><Plus size={16}/> Register application</button></div>
        {applications.length === 0 ? <section className="panel empty-apps"><Boxes size={25}/><h3>No applications registered</h3><p>Connect a GitHub repository and staging URL to begin.</p><button className="primary-button" onClick={() => navigate("/applications/new")}>Register first application</button></section>
          : <div className="application-list">{applications.map((application) => {
            const latestRun = runsByApplication[application.id];
            const isRunning = runningApplication === application.id;
            return <article className="panel application-card" key={application.id}>
              <div className="application-heading"><div><h3>{application.name}</h3><p>{application.description || "No description provided."}</p></div><span className="status">{application.status}</span></div>
              <div className="application-info"><span><strong>Repository</strong><a href={application.github_repository} target="_blank" rel="noreferrer">{application.github_repository}</a></span><span><strong>Branch</strong>{application.default_branch}</span><span><strong>Staging</strong><a href={application.staging_url} target="_blank" rel="noreferrer">{application.staging_url}</a></span></div>
              <div className="assessment-actions"><button className="primary-button" disabled={isRunning} onClick={() => void runAssessment(application)}><TestTube2 size={16}/>{isRunning ? "Checking repository and staging…" : "Run QA preflight"}</button>{latestRun && <span className={`assessment-status ${latestRun.status}`}>Latest: {latestRun.status}</span>}</div>
              {latestRun && <div className="assessment-results"><p>{latestRun.suite} · {new Date(latestRun.created_at).toLocaleString()}</p>
                {latestRun.executions.map((execution) => <div key={execution.id} className="execution-row"><span className={`result-dot ${execution.status}`}/><strong>{execution.test_name}</strong><span>{execution.status}</span><small>{execution.error_message || Object.entries(execution.actual).map(([key, value]) => `${key}: ${Array.isArray(value) ? value.join(", ") : String(value ?? "—")}`).join(" · ")}</small></div>)}
              </div>}
            </article>;
          })}</div>}
        <p className="scope-disclaimer">This preflight does not execute unit, integration, or browser test code. Full source test execution needs a separately isolated runner; the repository stays unchanged.</p>
      </div> : <div className="workspace">
        <div className="grid">
          {[
            ["Registered applications", dashboard?.total_applications],
            ["Active QA runs", dashboard?.active_runs],
            ["Passed tests", dashboard?.passed_tests],
            ["Failed tests", dashboard?.failed_tests],
            ["Critical defects", dashboard?.critical_defects],
          ].map(([label, value]) => <article className="card" key={String(label)}><p>{String(label)}</p><strong>{loading && !dashboard ? "…" : String(value ?? "—")}</strong></article>)}
        </div>
        <section className="panel"><div className="panelHeader panel-toolbar"><h2>Recent test runs</h2><button className="text-button" onClick={() => navigate("/applications")}>View applications</button></div>
          {dashboard?.recent_runs.length ? <table><thead><tr><th>Application</th><th>Run</th><th>Status</th><th>Created</th></tr></thead><tbody>{dashboard.recent_runs.map((run) => <tr key={run.id}><td>{run.application ?? "QA assessment"}</td><td>{run.id.slice(0, 8)}</td><td><span className="status">{run.status}</span></td><td>{new Date(run.created_at).toLocaleString()}</td></tr>)}</tbody></table> : <p className="empty">{loading ? "Loading recent runs…" : "No test runs recorded yet. Register an application and run its QA preflight to get started."}</p>}
        </section>
        <section className="scope-note home-note"><ShieldCheck size={17}/><span>Start with a GitHub repository and an authorized staging URL. Initial checks are non-destructive and do not execute repository code.</span><button className="text-button" onClick={() => navigate("/applications/new")}>Register application</button></section>
      </div>}
    </section>
  </main>;
}
