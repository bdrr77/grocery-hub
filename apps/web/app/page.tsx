"use client";

import { FormEvent, useEffect, useRef, useState } from "react";

type Item = { ean: string; label: string; quantity: number };
type Integration = { id: string; name: string; status: string; connected: boolean; message: string };

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api/v1/${path}`, { ...options, cache: "no-store" });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(typeof body?.detail === "string" ? body.detail : "La requête a échoué. Réessayez.");
  }
  return response.status === 204 ? undefined as T : response.json();
}

export default function Home() {
  const [items, setItems] = useState<Item[]>([]);
  const [connections, setConnections] = useState<Integration[]>([]);
  const [barcode, setBarcode] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [connectionError, setConnectionError] = useState("");
  const [notice, setNotice] = useState("");
  const pendingScan = useRef<{ barcode: string; event_id: string } | null>(null);
  const input = useRef<HTMLInputElement>(null);

  async function refresh() {
    setError("");
    setLoading(true);
    try { setItems(await api<Item[]>("items")); }
    catch (e) { setError((e as Error).message); }
    finally { setLoading(false); }
  }

  async function refreshConnections() {
    try {
      setConnections(await api<Integration[]>("integrations"));
      setConnectionError("");
    } catch {
      setConnectionError("Statut indisponible : impossible de joindre l’API.");
      setConnections([]);
    }
  }

  useEffect(() => {
    void refresh();
    void refreshConnections();
    const timer = setInterval(() => { void refreshConnections(); }, 30000);
    return () => clearInterval(timer);
  }, []);

  async function add(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    const code = barcode.trim();
    if (!/^[0-9]{8,32}$/.test(code)) {
      setError("Saisissez un code-barres de 8 à 32 chiffres.");
      return;
    }
    if (pendingScan.current?.barcode !== code) {
      pendingScan.current = { barcode: code, event_id: crypto.randomUUID() };
    }
    setBusy(true); setError(""); setNotice("");
    try {
      setItems(await api<Item[]>("items", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(pendingScan.current),
      }));
      pendingScan.current = null;
      setBarcode("");
      setNotice("Produit enregistré dans votre liste. Les paniers des magasins ne sont pas encore synchronisés.");
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  useEffect(() => { if (!busy) input.current?.focus(); }, [busy]);

  async function remove(ean: string) {
    setBusy(true); setError(""); setNotice("");
    try {
      await api<void>(`items/${ean}`, { method: "DELETE" });
      setItems(current => current.filter(item => item.ean !== ean));
      setNotice("Produit retiré de la liste.");
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  return <main>
    <header><div><p>Maison</p><h1>Courses</h1></div><button type="button" disabled={loading || busy} onClick={() => { void refresh(); void refreshConnections(); }}>Actualiser</button></header>
    <section aria-labelledby="connections-title" className="connections">
      <h2 id="connections-title">Connexions aux magasins</h2>
      <div className="connection-grid">{["Picnic", "Chronodrive"].map(name => {
        const connection = connections.find(store => store.name === name);
        return <article key={name}><div className="connection-heading"><strong>{name}</strong><span className="badge">{connection ? "Non disponible" : connectionError ? "Statut inconnu" : "Vérification…"}</span></div>
          <p>{connection?.message || connectionError || "Chargement du statut…"}</p></article>;
      })}</div>
      <p className="hint">Votre liste est enregistrée sur le serveur. Aucun prix ni panier magasin n’est disponible pour le moment.</p>
    </section>
    <form onSubmit={add} className="add">
      <label htmlFor="barcode">Ajouter par code-barres (EAN)</label>
      <div><input ref={input} id="barcode" inputMode="numeric" autoComplete="off" placeholder="Ex. 3017620422003" value={barcode} onChange={event => setBarcode(event.target.value)} disabled={busy} required aria-describedby="barcode-help"/><button disabled={busy || loading} type="submit">{busy ? "En cours…" : "Ajouter"}</button></div>
      <p id="barcode-help" className="hint">Tapez ou scannez un EAN, puis appuyez sur Entrée. Un nouvel ajout du même code augmente la quantité.</p>
    </form>
    {error && <p role="alert" className="error">{error}</p>}
    {notice && <p role="status" className="notice">{notice}</p>}
    <section aria-labelledby="list-title" aria-busy={loading}>
      <h2 id="list-title">Ma liste <span className="count">{items.reduce((sum, item) => sum + item.quantity, 0)} article(s)</span></h2>
      {loading ? <p role="status">Chargement de la liste…</p> : !items.length && !error ? <p className="empty">Votre liste est vide. Ajoutez votre premier code-barres ci-dessus.</p> : null}
      <div className="grid">{items.map(item => <article key={item.ean}>
        <h3>{item.label}</h3><p className="ean">EAN {item.ean}</p><div className="item-actions"><strong>Quantité : {item.quantity}</strong><button type="button" disabled={busy} aria-label={`Retirer ${item.label}`} onClick={() => void remove(item.ean)}>Retirer</button></div>
      </article>)}</div>
    </section>
    <footer>Les noms des produits sont recherchés dans <a href="https://world.openfoodfacts.org/" target="_blank" rel="noreferrer">Open Food Facts</a> (<a href="https://opendatacommons.org/licenses/odbl/1-0/">ODbL</a>). Un produit inconnu reste enregistré avec son EAN.</footer>
  </main>;
}
