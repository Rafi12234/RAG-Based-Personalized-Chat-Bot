import { useEffect, useState, type FormEvent } from "react";
import { api, jsonBody } from "./api";
import "./App.css";

type Role = "OWNER" | "ADMIN" | "MEMBER";
type Member = { role: Role; joinedAt: string; user: { id: string; name: string; email: string } };
type User = {
  id: string; name: string; email: string;
  memberships: { role: Role; organizationId: string; organization: { id: string; name: string } }[];
};
type Org = { id: string; name: string; role: Role; createdAt: string };
type KB = { id: string; name: string; description: string | null; _count?: { documents: number } };
type Doc = { id: string; title: string; createdAt: string; uploadedById: string;
  latestVersion: { filename: string; status: string; sizeBytes: number; version: number } | null };
type Reply<T> = { success: true } & T;

export default function App() {
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [user, setUser] = useState<User | null>(null);
  const [mode, setMode] = useState<"login" | "register">("login");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [organizationName, setOrganizationName] = useState("");
  const [orgs, setOrgs] = useState<Org[]>([]);
  const [orgId, setOrgId] = useState("");
  const [newOrgName, setNewOrgName] = useState("");
  const [members, setMembers] = useState<Member[]>([]);
  const [memberEmail, setMemberEmail] = useState("");
  const [memberRole, setMemberRole] = useState<"MEMBER" | "ADMIN">("MEMBER");
  const [bases, setBases] = useState<KB[]>([]);
  const [baseId, setBaseId] = useState("");
  const [baseName, setBaseName] = useState("");
  const [baseDescription, setBaseDescription] = useState("");
  const [docs, setDocs] = useState<Doc[]>([]);
  const [uploadFile, setUploadFile] = useState<File | null>(null);

  const selectedOrg = orgs.find(o => o.id === orgId);
  const selectedBase = bases.find(b => b.id === baseId);
  const isManager = selectedOrg?.role === "OWNER" || selectedOrg?.role === "ADMIN";

  function report(err: unknown) {
    setNotice("");
    setError(err instanceof Error ? err.message : "Something went wrong");
  }
  async function run(work: () => Promise<void>) {
    setBusy(true); setError(""); setNotice("");
    try { await work(); }
    catch (err) { report(err); }
    finally { setBusy(false); }
  }
  async function refreshOrgs(prefer?: string) {
    const reply = await api<Reply<{ organizations: Org[] }>>("/organizations");
    setOrgs(reply.organizations);
    setOrgId(current => {
      if (prefer && reply.organizations.some(o => o.id === prefer)) return prefer;
      return reply.organizations.some(o => o.id === current) ? current : reply.organizations[0]?.id ?? "";
    });
  }
  async function refreshBases(id: string, prefer?: string) {
    const reply = await api<Reply<{ knowledgeBases: KB[] }>>(`/knowledge-bases?organizationId=${encodeURIComponent(id)}`);
    setBases(reply.knowledgeBases);
    setBaseId(current => {
      if (prefer && reply.knowledgeBases.some(b => b.id === prefer)) return prefer;
      return reply.knowledgeBases.some(b => b.id === current) ? current : reply.knowledgeBases[0]?.id ?? "";
    });
  }
  async function refreshDocuments(id: string) {
    const reply = await api<Reply<{ documents: Doc[] }>>(`/knowledge-bases/${encodeURIComponent(id)}/documents`);
    setDocs(reply.documents);
  }
  async function refreshMembers(id: string) {
    const reply = await api<Reply<{ members: Member[] }>>(`/organizations/${encodeURIComponent(id)}/members`);
    setMembers(reply.members);
  }

  useEffect(() => {
    api<Reply<{ user: User }>>("/auth/me")
      .then(r => setUser(r.user))
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, []);
  useEffect(() => {
    if (user) refreshOrgs().catch(report);
  }, [user]);
  useEffect(() => {
    if (orgId) {
      refreshBases(orgId).catch(report);
      refreshMembers(orgId).catch(report);
    }
  }, [orgId]);
  useEffect(() => {
    if (baseId) refreshDocuments(baseId).catch(report);
  }, [baseId]);

  async function submitAuth(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    await run(async () => {
      const body = mode === "register"
        ? { name, email, password, organizationName }
        : { email, password };
      const result = await api<Reply<{ user: User }>>(`/auth/${mode}`, {
        method: "POST", ...jsonBody(body),
      });
      setUser(result.user);
      setPassword("");
      setNotice(`Welcome${mode === "register" ? " to DocMind-AI" : " back"}!`);
    });
  }
  async function logout() {
    await run(async () => {
      await api("/auth/logout", { method: "POST" });
      setUser(null); setPassword(""); setNotice("You have signed out.");
      setOrgs([]); setOrgId(""); setBases([]); setBaseId(""); setDocs([]); setMembers([]);
    });
  }
  async function createOrg(e: FormEvent) {
    e.preventDefault();
    await run(async () => {
      const r = await api<Reply<{ organization: { id: string } }>>("/organizations", {
        method: "POST", ...jsonBody({ name: newOrgName }),
      });
      setNewOrgName(""); await refreshOrgs(r.organization.id);
      setNotice("Workspace created.");
    });
  }
  async function createKB(e: FormEvent) {
    e.preventDefault();
    await run(async () => {
      const r = await api<Reply<{ knowledgeBase: { id: string } }>>("/knowledge-bases", {
        method: "POST", ...jsonBody({ organizationId: orgId, name: baseName, description: baseDescription }),
      });
      setBaseName(""); setBaseDescription("");
      await refreshBases(orgId, r.knowledgeBase.id);
      setNotice("Knowledge base created.");
    });
  }
  async function editKB() {
    if (!baseId || !selectedBase || !isManager) return;
    const name = window.prompt("Knowledge base name", selectedBase.name);
    if (name === null) return;
    const description = window.prompt("Knowledge base description", selectedBase.description ?? "");
    if (description === null) return;
    await run(async () => {
      await api(`/knowledge-bases/${baseId}`, {
        method: "PATCH", ...jsonBody({ name: name.trim(), description: description.trim() }),
      });
      await refreshBases(orgId, baseId);
      setNotice("Knowledge base updated.");
    });
  }
  async function deleteKB() {
    if (!baseId || !selectedBase || !isManager) return;
    if (!window.confirm(`Delete empty knowledge base "${selectedBase.name}"? Knowledge bases with any document history cannot be deleted.`)) return;
    await run(async () => {
      await api(`/knowledge-bases/${baseId}`, { method: "DELETE" });
      setBaseId("");
      await refreshBases(orgId);
      setNotice("Empty knowledge base deleted.");
    });
  }
  async function inviteMember(e: FormEvent) {
    e.preventDefault();
    await run(async () => {
      await api(`/organizations/${orgId}/members`, {
        method: "POST", ...jsonBody({ email: memberEmail, role: memberRole }),
      });
      setMemberEmail(""); await refreshMembers(orgId);
      setNotice("Existing user added to the workspace.");
    });
  }
  async function uploadDocument(e: FormEvent) {
    e.preventDefault();
    if (!uploadFile || !baseId) return;
    await run(async () => {
      const form = new FormData();
      form.append("file", uploadFile);
      await api(`/knowledge-bases/${baseId}/documents`, { method: "POST", body: form });
      setUploadFile(null);
      const input = document.getElementById("doc-upload") as HTMLInputElement | null;
      if (input) input.value = "";
      await refreshDocuments(baseId);
      setNotice("File stored securely. AI indexing will be added in Phase 2.");
    });
  }
  async function download(id: string) {
    await run(async () => {
      const result = await api<Reply<{ url: string }>>(`/documents/${id}/download`);
      window.location.assign(result.url);
    });
  }
  async function archive(id: string) {
    if (!window.confirm("Archive this document? It will disappear from the active list.")) return;
    await run(async () => {
      await api(`/documents/${id}`, { method: "DELETE" });
      await refreshDocuments(baseId);
      setNotice("Document archived. Physical retention/purge is configured in a later phase.");
    });
  }

  if (loading) return <div className="loading-screen">Loading DocMind-AI…</div>;

  if (!user) return (
    <main className="auth-screen">
      <div className="auth-brand"><span className="brand-mark">D</span> DocMind<span>AI</span></div>
      <section className="auth-panel">
        <p className="eyebrow">PRIVATE DOCUMENT INTELLIGENCE</p>
        <h1>{mode === "login" ? "Welcome back" : "Create your workspace"}</h1>
        <p className="subtle">Manage documents securely. AI-powered question answering arrives in the next phases.</p>
        <form className="stack" onSubmit={submitAuth}>
          {mode === "register" && <label>Your name<input required minLength={2} maxLength={120} value={name} onChange={e => setName(e.target.value)} /></label>}
          <label>Email address<input required type="email" maxLength={191} autoComplete="email" value={email} onChange={e => setEmail(e.target.value)} /></label>
          <label>Password<input required type="password" autoComplete={mode === "login" ? "current-password" : "new-password"} minLength={mode === "register" ? 12 : 1} value={password} onChange={e => setPassword(e.target.value)} /></label>
          {mode === "register" && <label>Workspace name<input required minLength={2} maxLength={150} value={organizationName} onChange={e => setOrganizationName(e.target.value)} /></label>}
          {error && <p role="alert" className="message error">{error}</p>}
          <button className="primary" disabled={busy}>{busy ? "Working…" : mode === "login" ? "Sign in" : "Create account"}</button>
        </form>
        <p className="auth-switch">{mode === "login" ? "New to DocMind-AI?" : "Already have an account?"} <button className="link-button" onClick={() => { setMode(mode === "login" ? "register" : "login"); setError(""); }}>{mode === "login" ? "Create account" : "Sign in"}</button></p>
      </section>
      <p className="auth-foot">Your documents remain isolated by workspace membership.</p>
    </main>
  );

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="side-brand"><span className="brand-mark">D</span> DocMind<span>AI</span></div>
        <p className="side-caption">DOCUMENT WORKSPACE</p>
        <label className="side-label">Active workspace
          <select value={orgId} onChange={e => { setOrgId(e.target.value); setBases([]); setBaseId(""); setDocs([]); setMembers([]); setUploadFile(null); }}>
            {orgs.map(o => <option key={o.id} value={o.id}>{o.name}</option>)}
          </select>
        </label>
        <p className="side-caption">KNOWLEDGE BASES</p>
        <nav className="base-nav" aria-label="Knowledge bases">
          {bases.map(b => <button key={b.id} className={b.id === baseId ? "selected" : ""} onClick={() => { setBaseId(b.id); setDocs([]); setUploadFile(null); }}><span className="small-icon">▤</span>{b.name}<span className="base-count">{b._count?.documents ?? 0}</span></button>)}
          {bases.length === 0 && <p className="side-empty">No knowledge bases yet</p>}
        </nav>
        <div className="side-profile"><div className="avatar">{user.name.slice(0, 1).toUpperCase()}</div><div className="side-user"><strong>{user.name}</strong><small>{user.email}</small></div><button title="Sign out" className="icon-button" onClick={logout} disabled={busy}>↪</button></div>
      </aside>
      <main className="main-content">
        <header className="topbar"><div><p className="eyebrow">PHASE 1 · DOCUMENT FOUNDATION</p><h1>{selectedBase?.name || "Workspace overview"}</h1><p className="subtle">{selectedOrg?.name ?? "Select a workspace"} · {selectedOrg?.role ?? ""}</p></div><span className="stage-label">Ingestion in Phase 2</span></header>
        {error && <p role="alert" className="message error">{error}</p>}
        {notice && <p role="status" className="message success">{notice}</p>}
        <div className="stat-grid">
          <div className="stat"><span>Knowledge bases</span><strong>{bases.length}</strong><small>Collections</small></div>
          <div className="stat"><span>Documents</span><strong>{bases.reduce((sum, b) => sum + (b._count?.documents ?? 0), 0)}</strong><small>Active in workspace</small></div>
          <div className="stat"><span>Members</span><strong>{members.length}</strong><small>With workspace access</small></div>
        </div>
        <div className="content-grid">
          <section className="panel wide"><div className="panel-heading"><div><h2>Documents</h2><p className="subtle">{selectedBase ? `${selectedBase.name} · supported formats: PDF, DOCX, TXT` : "Create or select a knowledge base to get started"}</p></div>{selectedBase && isManager && <div className="panel-actions"><button className="secondary" type="button" disabled={busy} onClick={editKB}>Edit collection</button><button className="secondary" type="button" disabled={busy} onClick={deleteKB}>Delete empty collection</button></div>}</div>
            {baseId ? <>
              <form className="upload-row" onSubmit={uploadDocument}><input id="doc-upload" aria-label="Choose document" type="file" accept=".pdf,.docx,.txt" onChange={e => setUploadFile(e.target.files?.[0] ?? null)} /><button className="primary" disabled={!uploadFile || busy}>{busy ? "Working…" : "Upload document"}</button></form>
              <div className="table-scroll"><table><thead><tr><th>Name</th><th>Status</th><th>Size</th><th>Uploaded</th><th>Actions</th></tr></thead><tbody>{docs.map(d => <tr key={d.id}><td className="file-cell"><span className="file-icon">▤</span><span>{d.title}</span></td><td><span className="status">{d.latestVersion?.status ?? "UNKNOWN"}</span></td><td>{d.latestVersion ? `${(d.latestVersion.sizeBytes / 1024 / 1024).toFixed(2)} MB` : "—"}</td><td>{new Date(d.createdAt).toLocaleDateString()}</td><td className="action-cell"><button disabled={busy} onClick={() => download(d.id)}>Download</button><button disabled={busy || (!isManager && d.uploadedById !== user.id)} title={!isManager && d.uploadedById !== user.id ? "Only the uploader or a manager can archive" : "Archive document"} onClick={() => archive(d.id)}>Archive</button></td></tr>)}{docs.length === 0 && <tr><td colSpan={5} className="empty-row">No files yet. Upload your first document above.</td></tr>}</tbody></table></div>
            </> : <div className="blank-state">Select or create a knowledge base to upload documents.</div>}
          </section>
          <section className="panel"><h2>New knowledge base</h2><p className="subtle">Organize documents into collections.</p>{isManager ? <form className="stack" onSubmit={createKB}><label>Collection name<input required minLength={2} maxLength={200} value={baseName} onChange={e => setBaseName(e.target.value)} placeholder="Project documentation" /></label><label>Description<textarea value={baseDescription} onChange={e => setBaseDescription(e.target.value)} maxLength={5000} placeholder="Optional description" rows={3} /></label><button className="primary" disabled={busy || !orgId}>Create knowledge base</button></form> : <p className="subtle">Only owners and admins can create collections.</p>}</section>
          <section className="panel"><h2>Workspace settings</h2><p className="subtle">Create another isolated workspace.</p><form className="stack" onSubmit={createOrg}><label>Workspace name<input required minLength={2} maxLength={150} placeholder="New workspace" value={newOrgName} onChange={e => setNewOrgName(e.target.value)} /></label><button className="secondary" disabled={busy}>Create workspace</button></form></section>
          <section className="panel"><h2>Team access</h2><p className="subtle">Add someone who already registered with DocMind-AI.</p><div className="members">{members.map(m => <div className="member" key={m.user.id}><div className="avatar small">{m.user.name[0]?.toUpperCase()}</div><div><strong>{m.user.name}</strong><small>{m.user.email}</small></div><span>{m.role}</span></div>)}</div>{isManager && <form className="stack" onSubmit={inviteMember}><label>Registered member's email<input type="email" required placeholder="teammate@example.com" value={memberEmail} onChange={e => setMemberEmail(e.target.value)} /></label><label>Role<select value={memberRole} onChange={e => setMemberRole(e.target.value as "MEMBER" | "ADMIN")}><option value="MEMBER">Member</option>{selectedOrg?.role === "OWNER" && <option value="ADMIN">Admin</option>}</select></label><button className="secondary" disabled={busy || !orgId}>Add member</button></form>}</section>
        </div>
      </main>
    </div>
  );
}
