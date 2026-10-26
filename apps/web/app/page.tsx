"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import ProductSearch, { ShopBadge } from "./product-search";

type Item = { ean: string; label: string; quantity: number; size?:string|null; source?:string|null; brand?:string|null };
type Lookup = {status:string;message:string;checked_at:number};
type SyncItem = { ean: string; quantity: number; status: string; message: string; product_id: string | null; product_name: string | null; price_cents: number | null; size?:string; unit_price_cents?:number|null; unit_price_unit?:string|null; ean_lookup?:Lookup|null };
type Pricing = {updating?:boolean;fresh:boolean;fetched_at:number|null;list_complete:boolean;list_total_cents:number|null;known_list_total_cents:number|null;priced_items:number;item_count:number;missing_items:number;cart_total_cents:number|null;deposit_cents:number|null;fees_cents:number|null;discount_cents:number|null;other_cart_items:number|null};
type Integration = { id: string; name: string; status: string; connected: boolean; configured: boolean; message: string; checked_at: number | null; items: SyncItem[]; pricing?:Pricing };

const money = (value: number) => new Intl.NumberFormat("fr-FR", { style: "currency", currency: "EUR" }).format(value / 100);
const connectionLabels: Record<string,string> = {not_configured:"À configurer", otp_required:"Code SMS requis", pending:"En attente", stale:"Worker à vérifier", busy:"En cours"};
const statusLabel = (store: Integration) => store.connected ? "Connecté" : (connectionLabels[store.status] || "Connexion en erreur");

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
  const [storeBusy, setStoreBusy] = useState("");
  const [otp, setOtp] = useState("");
  const pendingScan = useRef<{ value: string; event_id: string } | null>(null);
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
      setItems(await api<Item[]>("items"));
      setConnectionError("");
    } catch {
      setConnectionError("Statut indisponible : impossible de joindre l’API.");
      setConnections([]);
    }
  }

  useEffect(() => {
    void refresh();
    void refreshConnections();
    const timer = setInterval(() => { void refreshConnections(); }, 5000);
    return () => clearInterval(timer);
  }, []);

  async function add(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    const code = barcode.trim();
    if (!code || code.length>250 || (/^[0-9]+$/.test(code) && !/^[0-9]{8,32}$/.test(code))) {
      setError("Saisissez un nom de produit ou un code-barres de 8 à 32 chiffres.");
      return;
    }
    if (pendingScan.current?.value !== code) {
      pendingScan.current = { value: code, event_id: crypto.randomUUID() };
    }
    setBusy(true); setError(""); setNotice("");
    try {
      const isBarcode = /^[0-9]+$/.test(code);
      setItems(await api<Item[]>(isBarcode ? "items" : "items/named", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({...(isBarcode ? {barcode:code} : {label:code}),event_id:pendingScan.current.event_id}),
      }));
      pendingScan.current = null;
      setBarcode("");
      setNotice("Produit enregistré. Synchronisation en attente pour les magasins configurés.");
      void refreshConnections();
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); window.setTimeout(()=>input.current?.focus(),0); }
  }


  function markPricesUpdating() {
    setConnections(current=>current.map(store=>({...store,pricing:store.pricing && store.connected ? {...store.pricing,updating:true,fresh:false,list_total_cents:null,known_list_total_cents:null,cart_total_cents:null,deposit_cents:null,fees_cents:null,discount_cents:null,other_cart_items:null}:store.pricing})));
  }

  async function remove(ean: string) {
    setBusy(true); setError(""); setNotice("");
    try {
      markPricesUpdating();
      await api<void>(`items/${ean}`, { method: "DELETE" });
      setItems(current => current.filter(item => item.ean !== ean));
      setNotice("Produit retiré de la liste. Retrait des paniers associés en attente.");
      void refreshConnections();
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); await refreshConnections(); }
  }

  async function quantity(item:Item, value:number) {
    setBusy(true); setError(""); setNotice("");
    try {
      markPricesUpdating();
      setItems(await api<Item[]>(`items/${item.ean}`,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({quantity:value})}));
      setNotice("Quantité mise à jour. Synchronisation des paniers en attente.");
      await refreshConnections();
    } catch(e) {setError((e as Error).message);}
    finally {setBusy(false);}
  }

  async function storeAction(store: string, action: string, payload = {}) {
    setStoreBusy(`${store}/${action}`); setError(""); setNotice("");
    try {
      await api(`integrations/${store}/${action}`, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload)});
      setNotice(action === "send-code" ? "Code SMS demandé." : action === "sync" ? "Synchronisation demandée au worker." : action === "match" ? "Produit associé. Synchronisation en attente." : action === "refresh" ? "Prix des paniers actualisés." : "Connexion mise à jour.");
      if (action === "verify-code") setOtp("");
    } catch (e) { setError((e as Error).message); }
    finally { setStoreBusy(""); await refreshConnections(); }
  }

  const comparable = connections.length===2 && connections.every(store=>store.pricing?.list_complete && store.pricing.item_count===items.length && items.length>0);
  const difference = comparable ? Math.abs(connections[0].pricing!.list_total_cents! - connections[1].pricing!.list_total_cents!) : null;

  return <main>
    <header><div><p>Maison</p><h1>Courses</h1></div><button type="button" disabled={loading || busy} onClick={() => { void refresh(); void refreshConnections(); }}>Actualiser</button></header>
    <section aria-labelledby="connections-title" className="connections">
      <h2 id="connections-title">Connexions aux magasins</h2>
      <div className="connection-grid">{["Picnic", "Chronodrive"].map(name => {
        const connection = connections.find(store => store.name === name);
        return <article key={name}><div className="connection-heading"><strong>{name}</strong><span className={`badge ${connection?.connected ? "connected" : ""}`}>{connection ? statusLabel(connection) : connectionError ? "Statut inconnu" : "Vérification…"}</span></div>
          <p>{connection?.message || connectionError || "Chargement du statut…"}</p>
          {connection?.checked_at && <p className="checked">Dernière vérification : {new Date(connection.checked_at * 1000).toLocaleTimeString("fr-FR")}</p>}
          {connection?.configured && <div className="store-actions">
            <button type="button" disabled={!!storeBusy} onClick={() => void storeAction(connection.id,"connect")}>{storeBusy === `${connection.id}/connect` ? "Connexion…" : "Reconnecter"}</button>
            <button type="button" disabled={!!storeBusy || !connection.connected || connection.items.some(job=>job.status==="queued")} onClick={() => void storeAction(connection.id,"sync")}>Synchroniser</button>
            <button type="button" disabled={!!storeBusy || !connection.connected || connection.items.some(job=>job.status==="queued")} onClick={() => void storeAction(connection.id,"refresh")}>Actualiser les prix</button>
          </div>}
          {connection?.id === "picnic" && connection.status === "otp_required" && <div className="verification">
            <button type="button" disabled={!!storeBusy} onClick={() => void storeAction("picnic","send-code")}>Recevoir un code SMS</button>
            <form onSubmit={event => {event.preventDefault(); void storeAction("picnic","verify-code",{code:otp});}}>
              <label htmlFor="picnic-otp">Code de vérification Picnic</label>
              <input id="picnic-otp" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{4,10}" required value={otp} onChange={event=>setOtp(event.target.value)}/>
              <button disabled={!!storeBusy || !otp} type="submit">Vérifier le code</button>
            </form>
          </div>}
        </article>;
      })}</div>
      <p className="hint">Les produits identifiés par EAN sont synchronisés automatiquement. Choisissez les correspondances manquantes dans votre liste. Seuls les articles associés sont modifiés dans les paniers.</p>
    </section>
    <section className="comparison" aria-labelledby="comparison-title">
      <h2 id="comparison-title">Comparer mes courses</h2>
      <div className="comparison-grid">{connections.map(store=><article key={store.id}>
        <h3>{store.name}</h3>
        <p>Ma sélection <strong>{store.pricing?.updating ? <span className="shop-progress" role="status"><span className="loading-spinner" aria-hidden="true"/>Mise à jour…</span> : store.pricing?.list_total_cents != null && store.pricing.item_count===items.length ? money(store.pricing.list_total_cents) : "À compléter"}</strong></p>
        <small>{store.pricing?.priced_items || 0} / {items.length} produit(s) chiffré(s), quantités synchronisées.</small>
        {store.pricing?.known_list_total_cents != null && !store.pricing.list_complete && store.pricing.priced_items>0 && <small>Sous-total connu : {money(store.pricing.known_list_total_cents)}</small>}
        <p>Panier du magasin <strong>{store.pricing?.updating ? <span className="shop-progress" role="status"><span className="loading-spinner" aria-hidden="true"/>Mise à jour du panier…</span> : store.pricing?.cart_total_cents != null ? money(store.pricing.cart_total_cents) : "Indisponible"}</strong></p>
        {store.pricing?.deposit_cents != null && <small>Consignes : {money(store.pricing.deposit_cents)}</small>}
        {store.pricing?.fees_cents != null && <small>Frais signalés : {money(store.pricing.fees_cents)}</small>}
        {!!store.pricing?.discount_cents && <small>Remises signalées : {money(store.pricing.discount_cents)}</small>}
        {!!store.pricing?.other_cart_items && <small>{store.pricing.other_cart_items} autre(s) ligne(s) dans ce panier.</small>}
        <small>{store.pricing?.fetched_at ? `Relevé à ${new Date(store.pricing.fetched_at*1000).toLocaleTimeString("fr-FR")}${store.pricing.fresh ? "" : " · à actualiser"}` : "Aucun relevé de prix disponible."}</small>
      </article>)}</div>
      {difference != null && <p className="comparison-difference">Écart entre vos sélections : <strong>{money(difference)}</strong></p>}
      <p className="hint">Ma sélection additionne les lignes associées à votre liste. Le panier du magasin comprend aussi ses autres articles et ses frais. Vérifiez les formats choisis ; les remises peuvent dépendre du panier complet.</p>
    </section>
    <form onSubmit={add} className="add">
      <label htmlFor="barcode">Ajouter un produit ou un EAN</label>
      <div><input ref={input} id="barcode" autoComplete="off" placeholder="Ex. Nutella ou 3017620422003" value={barcode} onChange={event => setBarcode(event.target.value)} disabled={busy} required maxLength={250} aria-describedby="barcode-help"/><button disabled={busy || loading} type="submit">{busy ? "En cours…" : "Ajouter"}</button></div>
      <p id="barcode-help" className="hint">Tapez un nom ou scannez un EAN, puis appuyez sur Entrée. Choisissez ensuite le produit et le format dans chaque magasin.</p>
    </form>
    {error && <p role="alert" className="error">{error}</p>}
    {notice && <p role="status" className="notice">{notice}</p>}
    <section aria-labelledby="list-title" aria-busy={loading}>
      <h2 id="list-title">Ma liste <span className="count">{items.reduce((sum, item) => sum + item.quantity, 0)} article(s)</span></h2>
      {loading ? <p role="status">Chargement de la liste…</p> : !items.length && !error ? <p className="empty">Votre liste est vide. Ajoutez votre premier code-barres ci-dessus.</p> : null}
      <div className="grid">{items.map(item => <article key={item.ean}>
        <h3>{item.label}</h3>{item.brand && <p className="result-brand">{item.brand}</p>}{item.size && <p className="item-pack">{item.size}</p>}{item.source==="openfoodfacts" && <ShopBadge source="openfoodfacts"/>}<p className="ean">{item.ean.startsWith("n") ? "Produit ajouté par nom" : `EAN ${item.ean}`}</p><div className="item-actions"><div className="quantity-controls"><button type="button" disabled={busy || item.quantity<=1} aria-label={`Diminuer ${item.label}`} onClick={()=>void quantity(item,item.quantity-1)}>−</button><strong>Quantité : {item.quantity}</strong><button type="button" disabled={busy || item.quantity>=999} aria-label={`Augmenter ${item.label}`} onClick={()=>void quantity(item,item.quantity+1)}>+</button></div><button type="button" disabled={busy} aria-label={`Retirer ${item.label}`} onClick={() => void remove(item.ean)}>Retirer</button></div>
        <div className="item-stores">{connections.map(store => {
          const job = store.items.find(job => job.ean === item.ean);
          return <div key={store.id}><ShopBadge source={store.id}/>{job?.product_name && <span>{job.product_name}</span>}
            {store.configured && ["connected","pending","busy"].includes(store.status) && (!job || job.status==="queued") ? <small className="shop-progress" role="status"><span className="loading-spinner" aria-hidden="true"/>{job?.product_id ? "Mise à jour du panier et du prix…" : "Recherche du produit et du prix…"}</small> : <small>{!store.connected ? "Magasin déconnecté" : !job?.product_id ? "Recherchez un produit ci-dessous pour ce magasin." : job.status==="synced" ? "Dans votre panier" : "Mise à jour impossible. Réessayez depuis le magasin."}</small>}
            {job?.size && <small>Format : {job.size}</small>}
            {job?.price_cents != null && <small>Prix observé : {money(job.price_cents)} / article</small>}
            {job?.unit_price_cents != null && <small>{money(job.unit_price_cents)} / {job.unit_price_unit}</small>}
          </div>;
        })}</div>
        <ProductSearch lookupKey={connections.map(store=>{const job=store.items.find(job=>job.ean===item.ean);return [store.id,job?.status,job?.ean_lookup?.checked_at].join(":");}).join("|")} ean={item.ean} label={item.label} unresolved={(item.label.startsWith("EAN ") || (!item.source && item.ean.startsWith("n"))) && !connections.some(store=>store.items.some(job=>job.ean===item.ean && !!job.product_id))} pending={connections.some(store=>store.configured && ["connected","pending","busy"].includes(store.status) && store.items.some(job=>job.ean===item.ean && job.status==="queued"))} matches={Object.fromEntries(connections.map(store=>[store.id,store.items.find(job=>job.ean===item.ean)?.product_id || null]))} onChange={async()=>{setItems(await api<Item[]>("items"));await refreshConnections();}}/>
      </article>)}</div>
    </section>
    <footer>Les noms des produits sont recherchés dans <a href="https://world.openfoodfacts.org/" target="_blank" rel="noreferrer">Open Food Facts</a> (<a href="https://opendatacommons.org/licenses/odbl/1-0/">ODbL</a>). Un produit inconnu reste enregistré avec son EAN.</footer>
  </main>;
}
