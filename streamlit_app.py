import streamlit as st
import pandas as pd
from supabase import create_client, Client
from datetime import date
import random
import string
import time

# ─── CONFIG ───────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Bola Baja por Parejas ⛳", layout="wide")

# ─── SUPABASE ─────────────────────────────────────────────────────────────────
@st.cache_resource
def get_supabase() -> Client:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

supabase = get_supabase()

# ─── HELPERS ──────────────────────────────────────────────────────────────────
def get_players():
    res = supabase.table("players").select("id, name, current_handicap").order("name").execute()
    return res.data

def get_holes():
    res = supabase.table("holes").select("hole_number, par, handicap").order("hole_number").execute()
    return sorted(res.data, key=lambda x: x["hole_number"])

def get_tees():
    res = supabase.table("tees").select("id, name, color, rating, slope, par").execute()
    return res.data

def generate_access_code():
    return "LC-" + "".join(random.choices(string.digits, k=4))

def course_handicap(handicap_index: float, slope: float, rating: float, par: int) -> int:
    return round(handicap_index * (slope / 113) + (rating - par))

def strokes_given(player_ch: int, holes: list) -> dict:
    result = {}
    for h in holes:
        extras = 0
        if player_ch >= h["handicap"]:
            extras = 1
        if player_ch >= 18 + h["handicap"]:
            extras = 2
        if player_ch >= 36 + h["handicap"]:
            extras = 3
        result[h["hole_number"]] = extras
    return result

def net_score(gross: int, extras: int) -> int:
    return gross - extras

def get_active_tournaments():
    res = supabase.table("tournaments").select("id, name, date, access_code, format").order("date", desc=True).limit(10).execute()
    return res.data

def get_tournament_by_code(code: str):
    res = supabase.table("tournaments").select("*").eq("access_code", code.upper()).execute()
    return res.data[0] if res.data else None

def get_tournament_pairs(tournament_id: str):
    res = supabase.table("tournament_pairs").select("*").eq("tournament_id", tournament_id).order("pair_order").execute()
    return res.data

def get_tournament_scores(tournament_id: str):
    res = supabase.table("tournament_scores").select("*").eq("tournament_id", tournament_id).execute()
    return res.data

def save_hole_score(tournament_id: str, pair_name: str, player_id, guest_id, hole_number: int, strokes: int, net_strokes: int):
    # Upsert por tournament_id + pair_name + player/guest + hole_number
    existing = supabase.table("tournament_scores")\
        .select("id")\
        .eq("tournament_id", tournament_id)\
        .eq("pair_name", pair_name)\
        .eq("hole_number", hole_number)\
        .eq("player_id", player_id if player_id else "00000000-0000-0000-0000-000000000000")\
        .execute()

    data = {
        "tournament_id": tournament_id,
        "pair_name": pair_name,
        "player_id": player_id,
        "guest_id": guest_id,
        "hole_number": hole_number,
        "strokes": strokes,
        "net_strokes": net_strokes,
    }

    if existing.data:
        supabase.table("tournament_scores").update(data).eq("id", existing.data[0]["id"]).execute()
    else:
        supabase.table("tournament_scores").insert(data).execute()

# ─── SESSION STATE ─────────────────────────────────────────────────────────────
if "role" not in st.session_state:
    st.session_state.role = None          # "leader" | "spectator"
if "tournament_id" not in st.session_state:
    st.session_state.tournament_id = None
if "tournament_data" not in st.session_state:
    st.session_state.tournament_data = None
if "parejas" not in st.session_state:
    st.session_state.parejas = []
if "strokes_map" not in st.session_state:
    st.session_state.strokes_map = {}

# ══════════════════════════════════════════════════════════════════════════════
# PANTALLA DE ENTRADA
# ══════════════════════════════════════════════════════════════════════════════
if st.session_state.tournament_id is None:

    st.title("⛳ Bola Baja por Parejas — Las Cruces")
    st.markdown("---")

    col_left, col_right = st.columns(2)

    # ── LÍDER ──────────────────────────────────────────────────────────────
    with col_left:
        st.subheader("🏌️ Líder de Grupo")
        st.caption("Crea un nuevo torneo o continúa uno existente")

        with st.expander("➕ Crear nuevo torneo", expanded=True):
            players = get_players()
            tees = get_tees()
            holes = get_holes()

            fecha = st.date_input("📅 Fecha", value=date.today(), key="new_fecha")
            tee_options = {f"{t['color']} — Rating {t['rating']} / Slope {t['slope']}": t for t in tees}
            tee_label = st.selectbox("🏌️ Tee", list(tee_options.keys()), key="new_tee")
            tee = tee_options[tee_label]

            st.markdown("**Parejas**")
            player_options = {f"{p['name']} (HI: {p['current_handicap']})": p for p in players}
            player_labels = list(player_options.keys())

            num_parejas = st.number_input("Número de parejas", min_value=2, max_value=8, value=2, key="new_num_parejas")

            parejas_setup = []
            for i in range(int(num_parejas)):
                with st.expander(f"Pareja {i+1}", expanded=True):
                    c1, c2, c3 = st.columns(3)
                    with c1:
                        nombre = st.text_input("Nombre pareja", value=f"Pareja {i+1}", key=f"np_nombre_{i}")
                    with c2:
                        guest1 = st.checkbox("Invitado", key=f"np_guest1_{i}")
                        if guest1:
                            j1_name = st.text_input("Nombre", key=f"np_j1gname_{i}")
                            hi1 = st.number_input("HI", min_value=0.0, max_value=54.0, value=0.0, step=0.1, key=f"np_j1ghi_{i}")
                            j1 = {"id": f"guest_1_{i}", "name": j1_name or "Invitado 1", "current_handicap": hi1, "_is_guest": True}
                        else:
                            j1_label = st.selectbox("Jugador 1", player_labels, key=f"np_j1_{i}")
                            j1 = dict(player_options[j1_label])
                            if not j1["current_handicap"]:
                                hi1 = st.number_input(f"⚠️ HI de {j1['name']}", min_value=0.0, max_value=54.0, value=0.0, step=0.1, key=f"np_hi1_{i}")
                                j1["current_handicap"] = hi1
                                j1["_hi_temporal"] = True
                    with c3:
                        guest2 = st.checkbox("Invitado", key=f"np_guest2_{i}")
                        if guest2:
                            j2_name = st.text_input("Nombre", key=f"np_j2gname_{i}")
                            hi2 = st.number_input("HI", min_value=0.0, max_value=54.0, value=0.0, step=0.1, key=f"np_j2ghi_{i}")
                            j2 = {"id": f"guest_2_{i}", "name": j2_name or "Invitado 2", "current_handicap": hi2, "_is_guest": True}
                        else:
                            j2_label = st.selectbox("Jugador 2", player_labels, key=f"np_j2_{i}")
                            j2 = dict(player_options[j2_label])
                            if not j2["current_handicap"]:
                                hi2 = st.number_input(f"⚠️ HI de {j2['name']}", min_value=0.0, max_value=54.0, value=0.0, step=0.1, key=f"np_hi2_{i}")
                                j2["current_handicap"] = hi2
                                j2["_hi_temporal"] = True

                    par18 = tee["par"] if tee["par"] >= 60 else tee["par"] * 2
                    ch1 = course_handicap(float(j1["current_handicap"] or 0), tee["slope"], tee["rating"], par18 // 2)
                    ch2 = course_handicap(float(j2["current_handicap"] or 0), tee["slope"], tee["rating"], par18 // 2)
                    st.caption(f"Course HCP → {j1['name']}: **{ch1}** | {j2['name']}: **{ch2}**")
                    parejas_setup.append({"nombre": nombre, "j1": j1, "j2": j2, "ch1": ch1, "ch2": ch2})

            if st.button("🚀 Crear Torneo", type="primary"):
                holes = get_holes()
                # Generar código único
                code = generate_access_code()

                # Guardar invitados / handicaps temporales
                for p in parejas_setup:
                    for jkey in ["j1", "j2"]:
                        j = p[jkey]
                        if j.get("_is_guest"):
                            res = supabase.table("guests").insert({
                                "name": j["name"],
                                "handicap_index": float(j["current_handicap"] or 0),
                                "tournament_date": str(fecha),
                            }).execute()
                            p[jkey]["_guest_db_id"] = res.data[0]["id"]
                        elif j.get("_hi_temporal"):
                            res = supabase.table("guests").insert({
                                "name": j["name"],
                                "handicap_index": float(j["current_handicap"] or 0),
                                "tournament_date": str(fecha),
                                "player_id": j["id"],
                            }).execute()
                            p[jkey]["_guest_db_id"] = res.data[0]["id"]

                # Crear torneo
                t_res = supabase.table("tournaments").insert({
                    "name": f"Bola Baja Parejas — {fecha}",
                    "date": str(fecha),
                    "tee_id": tee["id"],
                    "format": "bola_baja_parejas",
                    "access_code": code,
                }).execute()
                tournament_id = t_res.data[0]["id"]

                # Guardar parejas en tournament_pairs
                for idx, p in enumerate(parejas_setup):
                    is_guest1 = p["j1"].get("_is_guest") or p["j1"].get("_hi_temporal")
                    is_guest2 = p["j2"].get("_is_guest") or p["j2"].get("_hi_temporal")
                    supabase.table("tournament_pairs").insert({
                        "tournament_id": tournament_id,
                        "pair_order": idx + 1,
                        "pair_name": p["nombre"],
                        "player1_id": None if is_guest1 else p["j1"]["id"],
                        "player1_guest_id": p["j1"].get("_guest_db_id"),
                        "player1_name": p["j1"]["name"],
                        "player1_ch": p["ch1"],
                        "player2_id": None if is_guest2 else p["j2"]["id"],
                        "player2_guest_id": p["j2"].get("_guest_db_id"),
                        "player2_name": p["j2"]["name"],
                        "player2_ch": p["ch2"],
                    }).execute()

                st.session_state.role = "leader"
                st.session_state.tournament_id = tournament_id
                st.session_state.tournament_data = {"tee": tee, "fecha": str(fecha), "code": code}
                st.session_state.parejas = parejas_setup
                st.session_state.strokes_map = {
                    p["nombre"]: {
                        "j1": strokes_given(p["ch1"], holes),
                        "j2": strokes_given(p["ch2"], holes),
                    } for p in parejas_setup
                }
                st.rerun()

        st.markdown("---")
        with st.expander("🔑 Continuar torneo con código"):
            code_input = st.text_input("Código del torneo (ej. LC-1234)", key="leader_code")
            if st.button("Entrar como Líder"):
                t = get_tournament_by_code(code_input)
                if t:\n                    holes = get_holes()\n                    pairs = get_tournament_pairs(t["id"])
                    tee_res = supabase.table("tees").select("*").eq("id", t["tee_id"]).execute()
                    tee = tee_res.data[0]
                    parejas = [{
                        "nombre": p["pair_name"],
                        "j1": {"id": p["player1_id"], "name": p["player1_name"], "current_handicap": None, "_guest_db_id": p["player1_guest_id"]},
                        "j2": {"id": p["player2_id"], "name": p["player2_name"], "current_handicap": None, "_guest_db_id": p["player2_guest_id"]},
                        "ch1": p["player1_ch"],
                        "ch2": p["player2_ch"],
                    } for p in pairs]
                    st.session_state.role = "leader"
                    st.session_state.tournament_id = t["id"]
                    st.session_state.tournament_data = {"tee": tee, "fecha": t["date"], "code": t["access_code"]}
                    st.session_state.parejas = parejas
                    st.session_state.strokes_map = {
                        p["nombre"]: {
                            "j1": strokes_given(p["ch1"], holes),
                            "j2": strokes_given(p["ch2"], holes),
                        } for p in parejas
                    }
                    st.rerun()
                else:
                    st.error("❌ Código no encontrado.")

    # ── ESPECTADOR ─────────────────────────────────────────────────────────
    with col_right:
        st.subheader("👀 Espectador")
        st.caption("Selecciona un torneo para ver el leaderboard")

        torneos = get_active_tournaments()
        if torneos:
            torneo_opts = {f"{t['name']} ({t['access_code']})": t for t in torneos}
            sel = st.selectbox("Torneos activos", list(torneo_opts.keys()), key="spec_select")
            if st.button("Ver Leaderboard", type="primary"):
                t = torneo_opts[sel]
                holes = get_holes()
                pairs = get_tournament_pairs(t["id"])
                tee_res = supabase.table("tees").select("*").eq("id", t["tee_id"]).execute()
                tee = tee_res.data[0]
                parejas = [{
                    "nombre": p["pair_name"],
                    "j1": {"id": p["player1_id"], "name": p["player1_name"], "current_handicap": None},
                    "j2": {"id": p["player2_id"], "name": p["player2_name"], "current_handicap": None},
                    "ch1": p["player1_ch"],
                    "ch2": p["player2_ch"],
                } for p in pairs]
                st.session_state.role = "spectator"
                st.session_state.tournament_id = t["id"]
                st.session_state.tournament_data = {"tee": tee, "fecha": t["date"], "code": t["access_code"]}
                st.session_state.parejas = parejas
                st.session_state.strokes_map = {
                    p["nombre"]: {
                        "j1": strokes_given(p["ch1"], holes),
                        "j2": strokes_given(p["ch2"], holes),
                    } for p in parejas
                }
                st.rerun()
        else:
            st.info("No hay torneos activos.")

# ══════════════════════════════════════════════════════════════════════════════
# PANTALLA DE TORNEO
# ══════════════════════════════════════════════════════════════════════════════
else:
    holes = get_holes()
    T = st.session_state.tournament_data
    parejas = st.session_state.parejas
    strokes_map = st.session_state.strokes_map
    role = st.session_state.role
    tournament_id = st.session_state.tournament_id

    tee = T["tee"]
    code = T.get("code", "")

    st.title(f"⛳ Bola Baja por Parejas — Las Cruces")
    col_info, col_exit = st.columns([4, 1])
    with col_info:
        st.caption(f"📅 {T['fecha']} | Tee: {tee['color']} | Rating: {tee['rating']} | Slope: {tee['slope']} | Código: **{code}**")
    with col_exit:
        if st.button("🚪 Salir"):
            for key in ["role", "tournament_id", "tournament_data", "parejas", "strokes_map"]:
                st.session_state[key] = None if key != "parejas" and key != "strokes_map" else ({} if key == "strokes_map" else [])
            st.rerun()

    st.markdown("---")

    # ── TABS ───────────────────────────────────────────────────────────────
    if role == "leader":
        tab_scores, tab_leaderboard = st.tabs(["📝 Scores", "🏆 Leaderboard"])
    else:
        tab_leaderboard = st.tabs(["🏆 Leaderboard"])[0]

    # ── TAB SCORES (solo líder) ────────────────────────────────────────────
    if role == "leader":
        with tab_scores:
            st.subheader("📝 Captura de Scores")

            hole_num = st.select_slider("Hoyo", options=list(range(1, 19)), value=1)
            hole_info = next(h for h in holes if h["hole_number"] == hole_num)
            st.markdown(f"### Hoyo {hole_num} — Par {hole_info['par']} | HCP Hoyo: {hole_info['handicap']}")

            # Cargar scores existentes de Supabase para este hoyo
            existing_scores = get_tournament_scores(tournament_id)
            existing_map = {}
            for s in existing_scores:
                key = (s["pair_name"], s["hole_number"], s["player_id"], s["guest_id"])
                existing_map[key] = s["strokes"]

            cols = st.columns(len(parejas))
            scores_to_save = []

            for i, pareja in enumerate(parejas):
                with cols[i]:
                    st.markdown(f"**{pareja['nombre']}**")
                    sg1 = strokes_map[pareja["nombre"]]["j1"][hole_num]
                    sg2 = strokes_map[pareja["nombre"]]["j2"][hole_num]

                    pid1 = pareja["j1"].get("id") if not pareja["j1"].get("_is_guest") else None
                    gid1 = pareja["j1"].get("_guest_db_id")
                    pid2 = pareja["j2"].get("id") if not pareja["j2"].get("_is_guest") else None
                    gid2 = pareja["j2"].get("_guest_db_id")

                    prev1 = existing_map.get((pareja["nombre"], hole_num, pid1, gid1), hole_info["par"])
                    prev2 = existing_map.get((pareja["nombre"], hole_num, pid2, gid2), hole_info["par"])

                    st.caption(f"{pareja['j1']['name']} (+{sg1})")
                    g1 = st.number_input(f"Golpes {pareja['j1']['name']}", min_value=1, max_value=15,
                        value=prev1, key=f"g1_{pareja['nombre']}_{hole_num}")

                    st.caption(f"{pareja['j2']['name']} (+{sg2})")
                    g2 = st.number_input(f"Golpes {pareja['j2']['name']}", min_value=1, max_value=15,
                        value=prev2, key=f"g2_{pareja['nombre']}_{hole_num}")

                    net1 = net_score(g1, sg1)
                    net2 = net_score(g2, sg2)
                    bola_baja = min(net1, net2)
                    st.metric("🏌️ Bola baja neta", bola_baja, delta=f"{bola_baja - hole_info['par']} vs par")

                    scores_to_save.append((pareja["nombre"], pid1, gid1, g1, net1))
                    scores_to_save.append((pareja["nombre"], pid2, gid2, g2, net2))

            if st.button(f"💾 Guardar Hoyo {hole_num}", type="primary"):
                for pair_name, pid, gid, strokes, net in scores_to_save:
                    save_hole_score(tournament_id, pair_name, pid, gid, hole_num, strokes, net)
                st.success(f"✅ Hoyo {hole_num} guardado.")

    # ── TAB LEADERBOARD ────────────────────────────────────────────────────
    with tab_leaderboard:
        if role == "spectator":
            st.subheader("🏆 Leaderboard en vivo")
            st.caption("Se actualiza automáticamente cada 30 segundos")

        scores_db = get_tournament_scores(tournament_id)

        # Construir leaderboard
        leaderboard = {}
        hoyos_jugados_map = {}

        for pareja in parejas:
            leaderboard[pareja["nombre"]] = 0
            hoyos_jugados_map[pareja["nombre"]] = set()

        # Agrupar por pareja y hoyo → bola baja neta
        from collections import defaultdict
        hole_scores = defaultdict(list)
        for s in scores_db:
            hole_scores[(s["pair_name"], s["hole_number"])].append(s["net_strokes"])

        for (pair_name, hn), nets in hole_scores.items():
            if pair_name in leaderboard and len(nets) >= 2:
                leaderboard[pair_name] += min(nets)
                hoyos_jugados_map[pair_name].add(hn)

        par_total = sum(h["par"] for h in holes)
        leader_data = []
        for pareja in parejas:
            hoyos = len(hoyos_jugados_map[pareja["nombre"]])
            total = leaderboard[pareja["nombre"]]
            par_jugado = sum(h["par"] for h in holes if h["hole_number"] in hoyos_jugados_map[pareja["nombre"]])
            vs_par = total - par_jugado if hoyos > 0 else 0
            leader_data.append({
                "Pareja": pareja["nombre"],
                "Jugadores": f"{pareja['j1']['name']} / {pareja['j2']['name']}",
                "Hoyos": f"{hoyos}/18",
                "Total Neto": total if hoyos > 0 else "-",
                "vs Par": f"{'+' if vs_par > 0 else ''}{vs_par}" if hoyos > 0 else "-",
            })

        leader_data.sort(key=lambda x: x["Total Neto"] if isinstance(x["Total Neto"], int) else 999)
        for i, r in enumerate(leader_data):
            r["Pos"] = i + 1

        if leader_data and isinstance(leader_data[0]["Total Neto"], int):
            ganador = leader_data[0]
            st.success(f"🥇 Líder: **{ganador['Pareja']}** — {ganador['vs Par']} ({ganador['Hoyos']} hoyos)")

        st.dataframe(pd.DataFrame(leader_data)[["Pos", "Pareja", "Jugadores", "Hoyos", "Total Neto", "vs Par"]],
            use_container_width=True, hide_index=True)

        if role == "spectator":
            time.sleep(30)
            st.rerun()
