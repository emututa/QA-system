"use client";

import { useEffect, useState } from "react";
import { Activity, Boxes, CircleAlert, Gauge, LayoutDashboard, Settings, ShieldCheck, TestTube2 } from "lucide-react";

type Dashboard = {
  total_applications: number;
  active_runs: number;
  passed_tests: number;
  failed_tests: number;
  critical_defects: number;
  recent_runs: Array<{ id: string; status: string; created_at: string }>;
};

const navigation = [
  [LayoutDashboard, "Overview"], [Boxes, "Applications"], [TestTube2, "Test Execution"],
  [Activity, "Monitoring"], [ShieldCheck, "Quality Gates"], [CircleAlert, "Defects"], [Settings, "Administration"],
];

export default function Home() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    fetch(`${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/api/dashboard/`)
      .then((response) => response.ok ? response.json() : Promise.reject(new Error("API unavailable")))
      .then(setData)
      .catch((cause) => setError(cause.message));
  }, []);

  const cards = data ? [
    ["Registered applications", data.total_applications], ["Active QA runs", data.active_runs],
    ["Passed tests", data.passed_tests], ["Failed tests", data.failed_tests], ["Critical defects", data.critical_defects],
  ] : [];

  return <main className="shell">
    <aside className="sidebar">
      <div className="brand"><Gauge size={22}/><strong>Zimworx QA</strong></div>
      <nav>{navigation.map(([Icon, label]) => <a key={String(label)} href="#" className={label === "Overview" ? "nav active" : "nav"}><Icon size={18}/><span>{String(label)}</span></a>)}</nav>
    </aside>
    <section className="content">
      <header className="topbar"><div><p className="eyebrow">Engineering intelligence</p><h1>Overview</h1></div><span className="environment">Staging</span></header>
      {error && <div className="notice">Live API data unavailable: {error}</div>}
      <div className="grid">{cards.map(([label, value]) => <article className="card" key={String(label)}><p>{String(label)}</p><strong>{String(value)}</strong></article>)}</div>
      <section className="panel"><div className="panelHeader"><h2>Recent test runs</h2></div>
        {data?.recent_runs.length ? <table><thead><tr><th>Run</th><th>Status</th><th>Created</th></tr></thead><tbody>{data.recent_runs.map((run) => <tr key={run.id}><td>{run.id.slice(0, 8)}</td><td><span className="status">{run.status}</span></td><td>{new Date(run.created_at).toLocaleString()}</td></tr>)}</tbody></table> : <p className="empty">No test runs recorded.</p>}
      </section>
    </section>
  </main>;
}
