const items=[
 {emoji:"🥛",name:"Lait",picnic:"1,15 €",chrono:"1,19 €"},
 {emoji:"🍝",name:"Spaghetti",picnic:"1,79 €",chrono:"1,65 €"},
 {emoji:"🍅",name:"Tomates",picnic:"3,20 €",chrono:"2,89 €"},
 {emoji:"🥤",name:"Coca Zero",picnic:"2,09 €",chrono:"2,25 €"}
];
export default function Home(){return <main><header><div><p>Maison</p><h1>Courses</h1></div><button>⚙️</button></header><section className="summary"><div>Picnic <b>72,64 €</b></div><div>Chronodrive <b>69,82 €</b></div><div>Écart <b>2,82 €</b></div></section><div className="add"><input placeholder="Ajouter un produit…"/><button>＋</button></div><section className="grid">{items.map(i=><article key={i.name}><span className="emoji">{i.emoji}</span><h2>{i.name}</h2><small>Picnic <b>{i.picnic}</b></small><small>Chrono <b>{i.chrono}</b></small></article>)}</section><nav>🛒 Liste　⚖️ Comparer　📦 Commandes</nav></main>}
