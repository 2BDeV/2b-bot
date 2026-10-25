import json
import os
import secrets
import unicodedata

from flask import Blueprint, jsonify, render_template_string, request

FLOWS_FILE = os.getenv("FLOWS_FILE", "flows.json")
ADMIN_KEY = os.getenv("FLOWS_ADMIN_KEY", "")
EXIT_WORDS = {"stop", "kilepes", "exit", "bye"}

bp = Blueprint("helpdesk", __name__)
flows = {}
sessions = {}


def norm(s):
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower().strip()


def authorized():
    key = request.headers.get("X-Admin-Key") or request.args.get("key", "")
    return bool(ADMIN_KEY) and secrets.compare_digest(key.encode(), ADMIN_KEY.encode())


def load_flows():
    global flows
    try:
        with open(FLOWS_FILE, encoding="utf-8") as f:
            flows = json.load(f)
    except FileNotFoundError:
        flows = {}
    except Exception as e:
        print(f"flows.json olvasasi hiba: {e}")


def save_flows():
    with open(FLOWS_FILE, "w", encoding="utf-8") as f:
        json.dump(flows, f, ensure_ascii=False, indent=2)


load_flows()


def find_trigger(text):
    n = norm(text)
    for flow in flows.values():
        if any(norm(t) == n for t in flow.get("triggers", [])):
            return flow
    return None


def match_option(node, text):
    opts = list(node.get("options", {}))
    n = norm(text)
    for i, o in enumerate(opts):
        if n == norm(o) or n == str(i + 1):
            return node["options"][o]
    for o in opts:
        if n and norm(o).startswith(n):
            return node["options"][o]
    return None


def collect(flow, node_id, user_id):
    texts = []
    while node_id and node_id in flow["nodes"]:
        node = flow["nodes"][node_id]
        if node["type"] == "question":
            sessions[user_id]["node"] = node_id
            opts = list(node.get("options", {}))
            texts.append(node.get("text", ""))
            texts += [f"{i + 1}. {o}" for i, o in enumerate(opts)]
            return "\n".join(t for t in texts if t)
        if node["type"] == "end":
            if node.get("text"):
                texts.append(node["text"])
            sessions.pop(user_id, None)
            return "\n".join(t for t in texts if t)
        texts.append(node.get("text", ""))
        node_id = node.get("next")
    sessions.pop(user_id, None)
    return "\n".join(t for t in texts if t)


def handle_message(user_id, content):
    text = content.strip()
    sess = sessions.get(user_id)
    if sess is None:
        flow = find_trigger(text)
        if flow is None:
            return None
        sessions[user_id] = {"flow": flow, "node": flow["start"]}
        return collect(flow, flow["start"], user_id)
    if norm(text) in EXIT_WORDS:
        sessions.pop(user_id, None)
        return "Helpdesk lezarva. 👋"
    flow = sess["flow"]
    node = flow["nodes"].get(sess["node"])
    if node is None:
        sessions.pop(user_id, None)
        return None
    nxt = match_option(node, text)
    if nxt is None:
        return "Nem ertettem 🙂 Valassz a felsorolt lehetosegek kozul: " + ", ".join(node.get("options", {}))
    return collect(flow, nxt, user_id)


@bp.route("/flows")
def editor():
    if not authorized():
        return "Nincs jogosultsag.", 403
    return render_template_string(EDITOR_HTML)


@bp.route("/api/flows")
def get_flows():
    if not authorized():
        return jsonify(error="Nincs jogosultsag."), 403
    return jsonify(flows)


@bp.route("/api/flows", methods=["POST"])
def post_flow():
    if not authorized():
        return jsonify(ok=False, error="Nincs jogosultsag."), 403
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    flow = data.get("flow")
    if not name or not isinstance(flow, dict) or not flow.get("nodes"):
        return jsonify(ok=False, error="Ervenytelen adat."), 400
    flows[name] = flow
    try:
        save_flows()
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500
    return jsonify(ok=True)


EDITOR_HTML = '''<!DOCTYPE html>
<html lang="hu"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Flow szerkesztő</title>
<style>
*{box-sizing:border-box}
body{margin:0;font-family:system-ui,sans-serif;background:#1e1f22;color:#eee;display:flex;height:100vh;overflow:hidden}
#toolbar{width:235px;padding:12px;background:#2b2d31;flex-shrink:0;overflow:auto}
#toolbar input,#toolbar select{width:100%;margin:3px 0;background:#1e1f22;color:#eee;border:1px solid #555;border-radius:4px;padding:4px}
#wrap{flex:1;overflow:auto;position:relative;background-image:radial-gradient(#2f3136 1px,transparent 1px);background-size:22px 22px}
.node{position:absolute;width:250px;background:#2b2d31;border:2px solid #5865f2;border-radius:8px;padding:8px;user-select:none}
.node textarea,.node input,.node select{width:100%;background:#1e1f22;color:#eee;border:1px solid #555;border-radius:4px;padding:4px;margin:3px 0;font:inherit}
.node h4{margin:0 0 4px;font-size:13px;display:flex;justify-content:space-between;align-items:center}
label{font-size:12px;color:#aaa}
button{background:#5865f2;border:0;color:#fff;border-radius:4px;padding:5px 7px;cursor:pointer;margin:2px}
.opt{display:flex;gap:3px;align-items:center}
svg{position:absolute;left:0;top:0;pointer-events:none}
</style></head><body>
<div id="toolbar">
<h3>🛠️ Flow szerkesztő</h3>
<label>Flow név</label><input id="flowName" value="main">
<label>Trigger szavak (vesszővel)</label><input id="triggers" value=":hi">
<label>Kezdő node</label><select id="startNode"></select><hr>
<button onclick="addNode('question')">❓ Kérdés</button>
<button onclick="addNode('message')">💬 Üzenet</button>
<button onclick="addNode('end')">🏁 Befejezés</button><hr>
<button onclick="save()">💾 Mentés</button>
<button onclick="load()">🔄 Betöltés</button>
</div>
<div id="wrap"><svg id="wires" width="5000" height="5000"></svg></div>
<script>
const KEY=new URLSearchParams(location.search).get('key')||'';
const H={'X-Admin-Key':KEY};
let nodes=[],nextId=1;
const uid=()=>'n'+(nextId++);
function addNode(type,data){
 const n=Object.assign({id:uid(),type,x:60+nodes.length*45,y:60+nodes.length*70,text:'',options:[],next:''},data||{});
 nodes.push(n);render();
}
function targetSelect(){
 const sel=document.createElement('select');
 const e=document.createElement('option');e.value='';e.textContent='-';sel.appendChild(e);
 for(const n of nodes){const o=document.createElement('option');o.value=n.id;o.textContent=n.id;sel.appendChild(o)}
 return sel;
}
function render(){
 document.querySelectorAll('.node').forEach(e=>e.remove());
 for(const n of nodes){
  const d=document.createElement('div');d.className='node';d.style.left=n.x+'px';d.style.top=n.y+'px';
  const h=document.createElement('h4');
  h.textContent={question:'❓ Kérdés',message:'💬 Üzenet',end:'🏁 Befejezés'}[n.type]+' ('+n.id+')';
  const del=document.createElement('button');del.textContent='✕';
  del.style.cssText='background:none;border:0;color:#f23f42;cursor:pointer;font-weight:bold';
  del.onclick=()=>{nodes=nodes.filter(x=>x!==n);render()};
  h.appendChild(del);d.appendChild(h);
  const ta=document.createElement('textarea');ta.rows=3;ta.placeholder='Küldendő szöveg';ta.value=n.text;
  ta.oninput=()=>n.text=ta.value;d.appendChild(ta);
  if(n.type==='question'){
   for(const o of n.options){
    const row=document.createElement('div');row.className='opt';
    const inp=document.createElement('input');inp.placeholder='Válasz (pl. magyar)';inp.value=o.label;
    inp.oninput=()=>o.label=inp.value;
    const sel=targetSelect();sel.value=o.target;sel.onchange=()=>{o.target=sel.value;drawWires()};
    const rm=document.createElement('button');rm.textContent='✕';rm.onclick=()=>{n.options=n.options.filter(x=>x!==o);render()};
    row.append(inp,sel,rm);d.appendChild(row);
   }
   const add=document.createElement('button');add.textContent='+ opció';
   add.onclick=()=>{n.options.push({label:'',target:''});render()};d.appendChild(add);
  }
  if(n.type==='message'){
   const l=document.createElement('label');l.textContent='Következő node';
   const sel=targetSelect();sel.value=n.next;sel.onchange=()=>{n.next=sel.value;drawWires()};
   d.append(l,sel);
  }
  d.addEventListener('mousedown',e=>{if(!['TEXTAREA','INPUT','SELECT'].includes(e.target.tagName)){e.preventDefault();startDrag(e,n,d)}});
  document.getElementById('wrap').appendChild(d);
 }
 const starts=document.getElementById('startNode'),cur=starts.value;
 starts.innerHTML='';
 for(const n of nodes){const o=document.createElement('option');o.value=n.id;o.textContent=n.id+' ('+n.type+')';starts.appendChild(o)}
 starts.value=cur;
 drawWires();
}
function startDrag(e,n,el){
 const sx=e.clientX-n.x,sy=e.clientY-n.y;
 const move=ev=>{n.x=ev.clientX-sx;n.y=ev.clientY-sy;el.style.left=n.x+'px';el.style.top=n.y+'px';drawWires()};
 const up=()=>{document.removeEventListener('mousemove',move);document.removeEventListener('mouseup',up)};
 document.addEventListener('mousemove',move);document.addEventListener('mouseup',up);
}
function drawWires(){
 const link=(a,b)=>{
  const x1=a.x+250,y1=a.y+25,x2=b.x,y2=b.y+25;
  return '<path d="M'+x1+' '+y1+' C '+(x1+60)+' '+y1+', '+(x2-60)+' '+y2+', '+x2+' '+y2+'" stroke="#5865f2" stroke-width="2" fill="none" marker-end="url(#ar)"/>';
 };
 let s='';
 for(const n of nodes){
  if(n.type==='question')for(const o of n.options)if(o.target){const b=nodes.find(x=>x.id===o.target);if(b)s+=link(n,b)}
  if(n.type==='message'&&n.next){const b=nodes.find(x=>x.id===n.next);if(b)s+=link(n,b)}
 }
 document.getElementById('wires').innerHTML='<defs><marker id="ar" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" fill="#5865f2"/></marker></defs>'+s;
}
function save(){
 const start=document.getElementById('startNode').value;
 if(!start){alert('Válassz kezdő node-ot!');return}
 const flow={triggers:document.getElementById('triggers').value.split(',').map(s=>s.trim()).filter(Boolean),start,nodes:{}};
 for(const n of nodes){
  const o={type:n.type,text:n.text,x:n.x,y:n.y};
  if(n.type==='question')o.options=Object.fromEntries(n.options.filter(x=>x.label&&x.target).map(x=>[x.label,x.target]));
  if(n.type==='message')o.next=n.next||null;
  flow.nodes[n.id]=o;
 }
 fetch('/api/flows',{method:'POST',headers:Object.assign({'Content-Type':'application/json'},H),body:JSON.stringify({name:document.getElementById('flowName').value.trim()||'main',flow})})
  .then(r=>r.json()).then(j=>j.ok?alert('Mentve!'):alert('Hiba: '+j.error));
}
function load(){
 const name=document.getElementById('flowName').value.trim()||'main';
 fetch('/api/flows',{headers:H}).then(r=>r.json()).then(all=>{
  const f=all[name];
  if(!f){alert('Nincs ilyen flow: '+name);return}
  document.getElementById('triggers').value=(f.triggers||[]).join(',');
  nodes=[];nextId=1;
  for(const[id,n]of Object.entries(f.nodes)){
   const num=parseInt(id.slice(1))||0;if(num>=nextId)nextId=num+1;
   nodes.push({id,type:n.type,x:n.x||80,y:n.y||80,text:n.text||'',options:n.options?Object.entries(n.options).map(([label,target])=>({label,target})):[],next:n.next||''});
  }
  render();
  document.getElementById('startNode').value=f.start||'';
 });
}
load();
</script></body></html>'''
