"use client";

import {useEffect,useState} from "react";
import ProductSearch, {type Search} from "../product-search";

type Item={ean:string;label:string};
export default function SearchPage() {
  const [item,setItem]=useState<Item|null>(null);
  const [query,setQuery]=useState("");
  const [error,setError]=useState("");
  const [initialData,setInitialData]=useState<Search|null>(null);
  useEffect(()=>{
    const params=new URLSearchParams(window.location.search);
    setQuery((params.get("q") || "").slice(0,200));
    void fetch("/api/v1/items",{cache:"no-store"}).then(async response=>{
      if(!response.ok)throw new Error("Impossible de charger votre liste.");
      const items:Item[]=await response.json();
      const selected=items.find(item=>item.ean===params.get("ean"));
      if(!selected)throw new Error("Ce produit n’est plus dans votre liste.");
      if(params.get("saved")==="1") {
        const saved=await fetch(`/api/v1/items/${selected.ean}/equivalents`,{cache:"no-store"});
        if(!saved.ok)throw new Error("Impossible de charger les résultats.");
        setInitialData(await saved.json());
      }
      setItem(selected);
    }).catch(error=>setError(error.message));
  },[]);
  return <main className="all-results"><header><h1>Tous les résultats</h1><a href="/">Ma liste</a></header>{error?<p role="alert">{error}</p>:item?<><p>Remplacer {item.label}. Votre quantité à acheter sera conservée.</p><ProductSearch ean={item.ean} label={item.label} matches={{}} unresolved pending={false} onChange={async()=>{}} fullPage initialQuery={query} initialData={initialData}/></>:<p role="status">Chargement…</p>}</main>;
}
