"""Deadline Quest - game menyelesaikan deadline (Python + Streamlit).

Jalankan:
    pip install streamlit anthropic
    export ANTHROPIC_API_KEY=sk-ant-...   # opsional, tanpa ini pakai quest dasar
    streamlit run app.py
"""
import html
import json
import os
import re
import uuid
from datetime import datetime, timedelta

import streamlit as st
import streamlit.components.v1 as components

DB = "data.json"
MODEL = "claude-sonnet-5-5"

# ---- pengaturan peliharaan (ubah di sini kalau tim mau menyeimbangkan) ----
STAGES = [("Telur", "🥚"), ("Anak", "🐣"), ("Remaja", "🐥"), ("Dewasa", "🐲")]
NEED = [100, 200, 300]   # XP yang dibutuhkan untuk evolusi tahap 1, 2, 3 (per tahap)
BADGE_COST = 100         # XP per badge Commitment setelah evolusi terakhir
TAP_XP = 2               # XP yang terpakai setiap kali tap / tekan spasi

st.set_page_config(page_title="Deadline Quest", page_icon="🎯", layout="centered")

st.markdown("""
<style>
.block-container{max-width:520px;padding-top:4.5rem}
.hud{background:linear-gradient(135deg,#6c5ce7,#a855f7);color:#fff;border-radius:22px;padding:16px;margin-bottom:16px}
.hud .r{display:flex;justify-content:space-between;font-weight:700}
.hud .bar{height:8px;border-radius:9px;background:rgba(255,255,255,.3);margin-top:10px;overflow:hidden}
.hud .bar i{display:block;height:100%;background:#fff;border-radius:9px}
.card{background:rgba(128,128,128,.12);border-radius:20px;padding:16px;margin-top:10px}
.card .top{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}
.card b{font-size:17px}
.pill{font-size:12px;font-weight:700;padding:5px 10px;border-radius:99px;color:#fff;white-space:nowrap}
.pb{height:7px;background:rgba(128,128,128,.25);border-radius:9px;margin-top:12px;overflow:hidden}
.pb i{display:block;height:100%;border-radius:9px}
.meta{display:flex;justify-content:space-between;opacity:.65;font-size:13px;margin-top:8px}
.big{font-size:44px;font-weight:800;letter-spacing:-1px}
.pet{font-size:110px;line-height:1.1;text-align:center}
div.stButton>button{border-radius:14px;font-weight:600;width:100%}
</style>
""", unsafe_allow_html=True)

fragment = getattr(st, "fragment", None) or getattr(st, "experimental_fragment", lambda f: f)


# ---------- data ----------
def default_db():
    return {"dl": [], "xp": 0, "streak": 0, "last": "", "done": 0,
            "pet": {"stage": 0, "fed": 0, "badges": 0}}


def load():
    base = default_db()
    try:
        with open(DB, encoding="utf-8") as f:
            base.update(json.load(f))
    except (OSError, ValueError):
        pass
    for k, v in default_db()["pet"].items():
        base["pet"].setdefault(k, v)
    return base


def save():
    with open(DB, "w", encoding="utf-8") as f:
        json.dump(st.session_state.db, f, ensure_ascii=False, indent=2)


if "db" not in st.session_state:
    st.session_state.db = load()
    st.session_state.view = "home"
    st.session_state.cur = None
db = st.session_state.db


def go(view, cur=None):
    st.session_state.view = view
    st.session_state.confirm_reset = False
    st.session_state.confirm_del = None
    if cur is not None:
        st.session_state.cur = cur


# ---------- helpers ----------
def progress(d):
    n = len(d["tasks"])
    return round(sum(t["done"] for t in d["tasks"]) / n * 100) if n else 0


def reward(d):
    """XP hadiah deadline (dari AI). Data lama tanpa 'reward' dihitung dari quest lamanya."""
    return d.get("reward") or max(50, sum(t.get("xp", 0) for t in d["tasks"]))


def time_left(d):
    delta = datetime.fromisoformat(d["due"]) - datetime.now()
    s = int(delta.total_seconds())
    if s <= 0:
        return "Lewat!"
    days, rem = divmod(s, 86400)
    hours, rem = divmod(rem, 3600)
    mins = rem // 60
    if days:
        return f"{days}h {hours}j"
    return f"{hours}j {mins}m" if hours else f"{mins} menit"


def color(d):
    h = (datetime.fromisoformat(d["due"]) - datetime.now()).total_seconds() / 3600
    return "#ef4444" if h < 24 else "#f59e0b" if h < 72 else "#22c55e"


def roman(n):
    out = ""
    for v, s in [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
                 (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]:
        while n >= v:
            out += s
            n -= v
    return out


def pet_need():
    s = db["pet"]["stage"]
    return NEED[s] if s < len(NEED) else BADGE_COST


def pet_goal_text():
    return "evolusi" if db["pet"]["stage"] < len(NEED) else "badge Commitment berikutnya"


def hud():
    p = db["pet"]
    need = pet_need()
    pct = min(100, round(p["fed"] / need * 100))
    name, emoji = STAGES[p["stage"]]
    st.markdown(
        f'<div class="hud"><div class="r"><span>{emoji} {name} · 💰 {db["xp"]} XP</span>'
        f'<span>🔥 {db["streak"]} hari</span></div>'
        f'<div class="bar"><i style="width:{pct}%"></i></div>'
        f'<div style="font-size:12px;margin-top:6px">{p["fed"]}/{need} XP ke {pet_goal_text()}</div></div>',
        unsafe_allow_html=True)


# ---------- AI ----------
def fallback(materi):
    topik = [x.strip() for x in re.split(r"[,;\n]+", materi) if x.strip()][:5]
    q = [{"t": f"Pelajari & rangkum: {x}", "min": 25} for x in topik]
    q.append({"t": "Kerjakan latihan soal campuran", "min": 40})
    q.append({"t": "Review catatan & rumus penting", "min": 15})
    total = sum(x["min"] for x in q)
    return max(50, min(300, round(total / 2 / 10) * 10)), q


def make_quests(title, due, materi):
    """Return (xp_hadiah_deadline, daftar_quest)."""
    jam = max(1, round((due - datetime.now()).total_seconds() / 3600))
    try:
        import anthropic
        client = anthropic.Anthropic()  # baca ANTHROPIC_API_KEY
        prompt = (
            f"Kamu pelatih belajar yang asik. Deadline: {title}. Sisa waktu: {jam} jam. "
            f"Materi: {materi}. Nilai tingkat kesulitan deadline ini (materi, beban, sisa waktu) "
            "dan tentukan hadiah XP: 50 (mudah) sampai 300 (sangat sulit). "
            "Lalu buat 4-7 quest kecil berurutan (pemahaman dasar, latihan, review) yang "
            "realistis untuk sisa waktu itu. Bahasa Indonesia, singkat, diawali kata kerja. "
            "Balas HANYA objek JSON: "
            '{"xp":120,"quests":[{"t":"teks quest","min":25}]} dengan min=menit (5-90).')
        r = client.messages.create(model=MODEL, max_tokens=1000,
                                   messages=[{"role": "user", "content": prompt}])
        raw = r.content[0].text
        data = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
        quests = data["quests"]
        xp = int(data["xp"])
        if not quests:
            raise ValueError
    except Exception:
        st.toast("AI tidak tersedia, pakai quest dasar", icon="⚠️")
        xp, quests = fallback(materi)
    xp = max(30, min(300, xp))
    return xp, [{"t": str(q.get("t", "Quest")),
                 "min": max(5, int(q.get("min", 20))),
                 "done": False} for q in quests[:8]]


# ---------- aksi ----------
def toggle(did, i):
    d = next(x for x in db["dl"] if x["id"] == did)
    t = d["tasks"][i]
    t["done"] = not t["done"]
    if t["done"]:
        today = datetime.now().date().isoformat()
        if db["last"] != today:
            yest = (datetime.now() - timedelta(days=1)).date().isoformat()
            db["streak"] = db["streak"] + 1 if db["last"] == yest else 1
            db["last"] = today
        # XP hanya diberikan sekali per deadline, saat semua quest selesai
        if progress(d) >= 100 and not d.get("claimed"):
            d["claimed"] = True
            db["xp"] += reward(d)
            db["done"] += 1
            st.toast(f"Deadline ditaklukkan! +{reward(d)} XP", icon="🏆")
            st.balloons()
    save()


def feed():
    p = db["pet"]
    n = min(TAP_XP, db["xp"])
    if n <= 0:
        st.toast("XP habis, selesaikan deadline dulu!", icon="🍽️")
        return
    db["xp"] -= n
    p["fed"] += n
    while p["fed"] >= pet_need():
        p["fed"] -= pet_need()
        if p["stage"] < len(NEED):
            p["stage"] += 1
            st.toast(f"Peliharaanmu berevolusi jadi {STAGES[p['stage']][0]}!", icon="✨")
        else:
            p["badges"] += 1
            st.toast(f"Badge Commitment {roman(p['badges'])} didapat!", icon="🎖️")
    save()


# ---------- halaman ----------
def page_home():
    st.title("Deadline Quest")
    st.caption("Selesaikan quest, kalahkan deadline.")
    hud()
    aktif = sorted((d for d in db["dl"] if progress(d) < 100), key=lambda d: d["due"])
    if not aktif:
        st.info("Tidak ada deadline aktif. Tekan ➕ untuk menambah.")
    for d in aktif:
        p, c = progress(d), color(d)
        n_done = sum(t["done"] for t in d["tasks"])
        st.markdown(
            f'<div class="card"><div class="top"><b>{html.escape(d["title"])}</b>'
            f'<span class="pill" style="background:{c}">{time_left(d)}</span></div>'
            f'<div class="pb"><i style="width:{p}%;background:{c}"></i></div>'
            f'<div class="meta"><span>{n_done}/{len(d["tasks"])} quest · 💰 {reward(d)} XP</span><span>{p}%</span></div></div>',
            unsafe_allow_html=True)
        st.button("Buka quest", key=f"o{d['id']}", on_click=go, args=("detail", d["id"]))
    selesai = [d for d in db["dl"] if progress(d) >= 100]
    if selesai:
        st.caption("Selesai ✅")
        for d in selesai:
            st.write(f"~~{d['title']}~~")


def page_detail():
    d = next((x for x in db["dl"] if x["id"] == st.session_state.cur), None)
    if not d:
        return go("home")
    st.button("‹ Kembali", on_click=go, args=("home",))
    st.caption(d["title"])
    st.markdown(f'<div class="big" style="color:{color(d)}">{time_left(d)}</div>', unsafe_allow_html=True)
    st.progress(progress(d) / 100)
    st.caption(f"💰 Hadiah: {reward(d)} XP kalau semua quest selesai (dinilai AI dari tingkat kesulitan)")
    st.subheader("Quest rekomendasi AI")
    for i, t in enumerate(d["tasks"]):
        st.checkbox(f"{t['t']}  ·  ⏱ {t['min']} mnt", value=t["done"],
                    key=f"{d['id']}_{i}_{t['done']}", on_change=toggle, args=(d["id"], i))
    if progress(d) >= 100:
        st.success("🏆 Deadline ditaklukkan!")
    if st.button("✨ Buat ulang quest dengan AI"):
        with st.spinner("AI menyusun quest..."):
            d["reward"], d["tasks"] = make_quests(d["title"], datetime.fromisoformat(d["due"]), d["materi"])
        save()
        st.rerun()
    if st.session_state.get("confirm_del") == d["id"]:
        st.warning("⚠️ Deadline ini beserta semua quest-nya akan dihapus permanen "
                   "dan tidak bisa dikembalikan. Yakin mau lanjut?")
        c1, c2 = st.columns(2)
        if c1.button("Ya, hapus", type="primary"):
            db["dl"] = [x for x in db["dl"] if x["id"] != d["id"]]
            save()
            go("home")
            st.rerun()
        if c2.button("Batal", key="cancel_del"):
            st.session_state.confirm_del = None
            st.rerun()
    else:
        st.button("Hapus deadline", on_click=lambda: st.session_state.update(confirm_del=d["id"]))


def page_add():
    st.button("‹ Batal", on_click=go, args=("home",))
    st.title("Deadline baru")
    title = st.text_input("Apa deadline-nya?", placeholder="Kuis Kalkulus besok")
    besok = datetime.now() + timedelta(days=1)
    c1, c2 = st.columns(2)
    tgl = c1.date_input("Tanggal", besok.date())
    jam = c2.time_input("Jam", besok.replace(minute=0, second=0, microsecond=0).time())
    materi = st.text_area("Materinya apa saja?", placeholder="Limit, turunan, aturan rantai, integral tak tentu...")
    if st.button("✨ Buat quest", type="primary"):
        if not (title.strip() and materi.strip()):
            st.warning("Isi judul dan materi dulu ya.")
            return
        due = datetime.combine(tgl, jam)
        with st.spinner("AI sedang menyusun quest..."):
            xp, tasks = make_quests(title.strip(), due, materi.strip())
        did = uuid.uuid4().hex[:8]
        db["dl"].append({"id": did, "title": title.strip(), "due": due.isoformat(),
                         "materi": materi.strip(), "tasks": tasks, "reward": xp, "claimed": False})
        save()
        go("detail", did)
        st.rerun()


@fragment
def pet_box():
    p = db["pet"]
    name, emoji = STAGES[p["stage"]]
    need = pet_need()
    st.markdown(f'<div class="pet">{emoji}</div>', unsafe_allow_html=True)
    st.markdown(f"<div style='text-align:center'><b>{name}</b> · 💰 {db['xp']} XP tersedia</div>",
                unsafe_allow_html=True)
    st.progress(min(1.0, p["fed"] / need))
    st.caption(f"{p['fed']}/{need} XP ke {pet_goal_text()}")
    st.button(f"🍖 Beri makan (-{TAP_XP} XP)", key="feed", on_click=feed)
    st.caption("Tap tombol berkali-kali atau tekan **spasi** untuk memberi makan.")


def page_pet():
    st.title("Peliharaan")
    pet_box()
    # tekan spasi = klik tombol "Beri makan" (memakai akses ke halaman induk Streamlit)
    components.html("""
<script>
const doc = window.parent.document;
if (!window.parent.__petKeys) {
  window.parent.__petKeys = true;
  const typing = () => ['INPUT', 'TEXTAREA'].includes(doc.activeElement.tagName);
  doc.addEventListener('keydown', e => {
    if (e.code !== 'Space' || typing()) return;
    e.preventDefault();
    if (e.repeat) return;
    const b = [...doc.querySelectorAll('button')].find(x => x.innerText.includes('Beri makan'));
    if (b) b.click();
  });
  doc.addEventListener('keyup', e => {
    if (e.code === 'Space' && !typing()) e.preventDefault();
  });
}
</script>""", height=0)

    n = db["pet"]["badges"]
    if n:
        st.subheader("Badge Commitment")
        st.markdown(" ".join(
            f'<span class="pill" style="background:#6c5ce7;display:inline-block;margin:3px">Commitment {roman(i)}</span>'
            for i in range(1, n + 1)), unsafe_allow_html=True)


def page_prof():
    st.title("Profil")
    st.caption("Pahlawan deadline")
    hud()
    total = sum(t["done"] for d in db["dl"] for t in d["tasks"])
    a, b = st.columns(2)
    a.metric("Quest selesai", total)
    b.metric("Deadline ditaklukkan", db["done"])
    a.metric("XP tersedia", db["xp"])
    b.metric("Badge Commitment", db["pet"]["badges"])
    a.metric("Streak hari", db["streak"])
    if st.session_state.get("confirm_reset"):
        st.warning("⚠️ Semua deadline, XP, streak, dan peliharaanmu akan dihapus permanen "
                   "dan tidak bisa dikembalikan. Yakin mau lanjut?")
        c1, c2 = st.columns(2)
        if c1.button("Ya, hapus semua", type="primary"):
            db.clear()
            db.update(default_db())
            save()
            st.session_state.confirm_reset = False
            st.rerun()
        if c2.button("Batal"):
            st.session_state.confirm_reset = False
            st.rerun()
    else:
        st.button("Reset semua data", on_click=lambda: st.session_state.update(confirm_reset=True))


# ---------- navigasi ----------
n1, n2, n3, n4 = st.columns(4)
n1.button("🎯 Deadline", on_click=go, args=("home",))
n2.button("➕ Tambah", on_click=go, args=("add",))
n3.button("🐾 Pet", on_click=go, args=("pet",))
n4.button("🏆 Profil", on_click=go, args=("prof",))

{"home": page_home, "detail": page_detail, "add": page_add,
 "pet": page_pet, "prof": page_prof}[st.session_state.view]()