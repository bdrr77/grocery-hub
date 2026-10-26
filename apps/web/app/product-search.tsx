"use client";

import { FormEvent, useEffect, useRef, useState } from "react";

type Offer = {source:string;id:string;name:string;size:string;eans?:string[];price_cents:number|null;unit_price_cents:number|null;unit_price_unit:string|null};
type Result = {name:string;brand?:string;size:string;codes:string[];offers:Offer[]};
type Search = {results:Result[];messages:{source:string;name:string;message:string}[]};
const names:Record<string,string> = {picnic:"Picnic",chronodrive:"Chronodrive",openfoodfacts:"Open Food Facts"};
const money = (cents:number) => new Intl.NumberFormat("fr-FR",{style:"currency",currency:"EUR"}).format(cents/100);

export function ShopBadge({source}:{source:string}) {
  return <span className={`shop-badge shop-${source}`}><span className="shop-mark" aria-hidden="true">{source==="picnic"?"P":source==="chronodrive"?"c":"●"}</span>{names[source] || source}</span>;
}

async function post<T>(path:string, data:unknown):Promise<T> {
  const response = await fetch(`/api/v1/${path}`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data)});
  if(!response.ok) {
    const body = await response.json().catch(()=>null);
    throw new Error(body?.detail || "Impossible de terminer cette action. Réessayez.");
  }
  return response.json();
}

export default function ProductSearch({ean,label,matches,unresolved,pending,onChange,fullPage=false,initialQuery=""}:{ean:string;label:string;matches:Record<string,string|null>;unresolved:boolean;pending:boolean;onChange:()=>Promise<void>;fullPage?:boolean;initialQuery?:string}) {
  const [activated,setActivated] = useState(fullPage);
  const [refreshing,setRefreshing] = useState(false);
  const [query,setQuery] = useState(initialQuery);
  const [open,setOpen] = useState(fullPage);
  const [searching,setSearching] = useState(false);
  const [data,setData] = useState<Search|null>(null);
  const [error,setError] = useState("");
  const [choosing,setChoosing] = useState("");
  const latest = useRef("");
  const last = useRef("");
  const request = useRef(false);
  const field = useRef<HTMLInputElement>(null);
  latest.current = query.trim();

  async function search(value:string) {
    if(request.current || value.length<2) return;
    request.current=true; last.current=value;
    setSearching(true);setError("");setData(null);setOpen(true);
    try {
      const found = await post<Search>(`items/${ean}/search`,{query:value});
      if(latest.current===value) setData(found);
    } catch(e) {if(latest.current===value)setError((e as Error).message);}
    finally {request.current=false;setSearching(false);}
  }

  useEffect(()=>{
    if(!open || searching || choosing || query.trim().length<2 || query.trim()===last.current) return;
    const timer=setTimeout(()=>void search(query.trim()),1000);
    return ()=>clearTimeout(timer);
  },[query,open,searching,choosing]);

  async function choose(offer:Offer) {
    setChoosing(`${offer.source}/${offer.id}`);setError("");
    try {
      if(offer.source==="openfoodfacts") {
        await post(`items/${ean}/identity`,{code:offer.id});
      } else {
        await post(`items/${ean}/select`,{source:offer.source,product_id:offer.id});
      }
      await onChange();
      setOpen(false);setActivated(false);
      if(fullPage)window.location.assign("/");
    } catch(e) {setError((e as Error).message);}
    finally {setChoosing("");}
  }

  function submit(event:FormEvent) {event.preventDefault();void search(query.trim());}
  async function refreshCard() {
    if(refreshing || searching || choosing || pending)return;
    setRefreshing(true);setError("");setOpen(false);
    try {
      const found=await post<Search>(`items/${ean}/refresh`,{});
      setData(found);setOpen(true);
      await onChange();
    } catch(e) {setError((e as Error).message);}
    finally {setRefreshing(false);}
  }
  const showing = open && (!!data || query.trim().length>=2);
  return <div className="card-search" onKeyDown={event=>{if(event.key==="Escape"){field.current?.focus();setOpen(false);}}}>
    {!fullPage && <div className="card-tools"><button type="button" disabled={pending || refreshing || searching || !!choosing} onClick={()=>void refreshCard()}>{refreshing?<><span className="loading-spinner" aria-hidden="true"/> Actualisation…</>:"Actualiser ce produit"}</button>{unresolved && !pending && !activated && <button type="button" onClick={()=>{setActivated(true);setOpen(true);setTimeout(()=>field.current?.focus(),0);}}>Rechercher un produit</button>}</div>}
    {refreshing && <p className="shop-progress" role="status">Vérification sur Open Food Facts et dans vos magasins…</p>}
    {unresolved && activated && !pending && <form onSubmit={submit}>
      <label htmlFor={`search-${ean}`}>Rechercher ce produit</label>
      <div className="card-search-field"><input ref={field} id={`search-${ean}`} autoComplete="off" placeholder="Nom, marque…" maxLength={200} value={query} aria-controls={showing?`results-${ean}`:undefined} onFocus={()=>setOpen(true)} onChange={event=>{last.current="";setQuery(event.target.value);setOpen(true);setData(null);setError("");}}/><button type="submit" disabled={searching || !!choosing || query.trim().length<2} aria-label={`Rechercher pour ${label}`}>⌕</button></div>
    </form>}
    {error && !showing && <p className="search-feedback" role="alert">{error}</p>}
    {showing && <div className="search-dropdown" id={`results-${ean}`}>
      <div className="search-dropdown-heading"><span>Résultats</span><button type="button" onClick={()=>setOpen(false)} aria-label="Fermer les résultats">×</button></div>
      {searching && <p className="shop-progress" role="status"><span className="loading-spinner" aria-hidden="true"/>Recherche dans vos magasins…</p>}
      {error && <p className="search-feedback" role="alert">{error}</p>}
      {!!data?.results.length && <ul aria-label="Produits trouvés">{(fullPage?data.results:data.results.slice(0,10)).map((result,index)=><li key={`${result.codes.join("-")}-${index}`}>
        <strong>{result.name}</strong>{result.brand && <span className="result-brand">{result.brand}</span>}{result.size && <span className="result-size">{result.size}</span>}
        <div className="result-offers">{result.offers.map(offer=>{
          const selected=offer.source!=="openfoodfacts" && matches[offer.source]===offer.id;
          const hasCode=offer.source==="openfoodfacts" || !!offer.eans?.length;
          return <button key={`${offer.source}/${offer.id}`} type="button" className={`offer-choice${selected?" selected":""}`} disabled={!!choosing || refreshing || !hasCode || selected || (offer.source!=="openfoodfacts" && !!matches[offer.source])} onClick={()=>void choose(offer)} aria-label={`Choisir ${offer.name} — ${names[offer.source]}`}>
            <ShopBadge source={offer.source}/><span className="offer-price">{offer.price_cents!=null?money(offer.price_cents):offer.source==="openfoodfacts"?"Choisir ce produit":"Prix indisponible"}</span>
            {!hasCode && <small>Code-barres indisponible</small>}
            {offer.unit_price_cents!=null && <small>{money(offer.unit_price_cents)} / {offer.unit_price_unit}</small>}
            {offer.size!==result.size && offer.size && <small>{offer.size}</small>}
            {offer.name!==result.name && <small>{offer.name}</small>}
            {selected && <small>✓ Dans votre liste</small>}
          </button>;
        })}</div>
      </li>)}</ul>}
      {!fullPage && !!data?.results.length && <p><a href={`/search?ean=${encodeURIComponent(ean)}&q=${encodeURIComponent(query || label)}`} target="_blank" rel="noopener noreferrer">Voir tous les résultats dans une nouvelle fenêtre ({data.results.length}) ↗</a></p>}
      {data && !data.results.length && <p>Aucun produit trouvé. Essayez un autre nom ou une marque.</p>}
      {data?.messages.map(message=><p className="search-feedback" key={message.source}><ShopBadge source={message.source}/> {message.message}</p>)}
      <p className="search-help">Choisir un résultat remplace ce produit et son code-barres. La quantité à acheter est conservée.</p>
    </div>}
  </div>;
}
