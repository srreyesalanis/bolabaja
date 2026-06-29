import streamlit as st
import pandas as pd
from supabase import create_client, Client
from datetime import date
import math

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

def course_handicap(handicap_index: float, slope: float, rating: float, par: int) -> int:
    """USGA Course Handicap = round(HI * Slope/113 + (CR - Par))"""
    return round(handicap_index * (slope / 113) + (rating - par))

def strokes_given(player_ch: int, holes: list) -> dict:
    """
    Regresa un dict {hole_number: extra_strokes} para el jugador.
    Si course_handicap = 20 → 1 golpe en los hoyos con handicap 1-18,
    más 1 extra en los hoyos con handicap 1-2 (total 2 golpes en esos hoyos).
    """
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

# ─── SESSION STATE ─────────────────────────────────────────────────────────────
if "torneo" not in st.session_state:
    st.session_state.torneo = {
        "parejas": [],      # [{id, nombre, j1, j2, ch1, ch2}]
        "scores": {},       # {pareja_id: {hole: {j1: gross, j2: gross}}}
        "tee": None,
        "fecha": date.today(),
        "iniciado": False,
    }

T = st.session_state.torneo

# ─── UI ───────────────────────────────────────────────────────────────────────
st.title("⛳ Bola Baja por Parejas — Las Cruces")

tab_setup, tab_scores, tab_resultados = st.tabs(["🛠 Configuración", "📝 Scores", "🏆 Resultados"])

# ══════════════════════════════════════════════════════════════════════════════
# TAB 1: CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════════════════════
with tab_setup:
    st.header("Configurar Torneo")

    players = get_players()
    tees = get_tees()
    holes = get_holes()

    col1, col2 = st.columns(2)
    with col1:
        T["fecha"] = st.date_input("📅 Fecha", value=T["fecha"])
    with col2:
        tee_options = {f"{t['color']} ({t['name']}) — Rating {t['rating']} / Slope {t['slope']}": t for t in tees}
        tee_label = st.selectbox("🏌️ Tee", list(tee_options.keys()))
        T["tee"] = tee_options[tee_label]

    st.divider()
    st.subheader("Registrar Parejas")

    player_options = {f"{p['name']} (HI: {p['current_handicap']})": p for p in players}
    player_labels = list(player_options.keys())

    num_parejas = st.number_input("Número de parejas", min_value=2, max_value=8, value=max(2, len(T["parejas"])))

    # Rebuild parejas list if size changed
    while len(T["parejas"]) < num_parejas:
        pid = len(T["parejas"]) + 1
        T["parejas"].append({"id": pid, "nombre": f"Pareja {pid}", "j1": None, "j2": None, "ch1": 0, "ch2": 0})
    while len(T["parejas"]) > num_parejas:
        T["parejas"].pop()

    for i, pareja in enumerate(T["parejas"]):
        with st.expander(f"Pareja {i+1}: {pareja['nombre']}", expanded=not T["iniciado"]):
            c1, c2, c3 = st.columns(3)
            with c1:
                pareja["nombre"] = st.text_input("Nombre pareja", value=pareja["nombre"], key=f"pnombre_{i}")

            # ── Jugador 1 ──────────────────────────────────────────────────
            with c2:
                guest1 = st.checkbox("Invitado", key=f"guest1_{i}")
                if guest1:
                    j1_name = st.text_input("Nombre", key=f"j1_gname_{i}")
                    hi1 = st.number_input("Handicap Index", min_value=0.0, max_value=54.0, value=0.0, step=0.1, key=f"j1_ghi_{i}")
                    pareja["j1"] = {"id": f"guest_1_{i}", "name": j1_name or "Invitado 1", "current_handicap": hi1}
                else:
                    j1_label = st.selectbox("Jugador 1", player_labels, key=f"j1_{i}",
                        index=next((j for j, l in enumerate(player_labels) if pareja["j1"] and player_options[l].get("id") == pareja["j1"].get("id")), 0))
                    pareja["j1"] = player_options[j1_label]
                    hi1 = pareja["j1"]["current_handicap"]
                    if not hi1:
                        hi1 = st.number_input(
                            f"⚠️ Handicap Index de {pareja['j1']['name']}",
                            min_value=0.0, max_value=54.0, value=0.0, step=0.1,
                            key=f"hi1_manual_{i}"
                        )

            # ── Jugador 2 ──────────────────────────────────────────────────
            with c3:
                guest2 = st.checkbox("Invitado", key=f"guest2_{i}")
                if guest2:
                    j2_name = st.text_input("Nombre", key=f"j2_gname_{i}")
                    hi2 = st.number_input("Handicap Index", min_value=0.0, max_value=54.0, value=0.0, step=0.1, key=f"j2_ghi_{i}")
                    pareja["j2"] = {"id": f"guest_2_{i}", "name": j2_name or "Invitado 2", "current_handicap": hi2}
                else:
                    j2_label = st.selectbox("Jugador 2", player_labels, key=f"j2_{i}",
                        index=next((j for j, l in enumerate(player_labels) if pareja["j2"] and player_options[l].get("id") == pareja["j2"].get("id")), 0))
                    pareja["j2"] = player_options[j2_label]
                    hi2 = pareja["j2"]["current_handicap"]
                    if not hi2:
                        hi2 = st.number_input(
                            f"⚠️ Handicap Index de {pareja['j2']['name']}",
                            min_value=0.0, max_value=54.0, value=0.0, step=0.1,
                            key=f"hi2_manual_{i}"
                        )

            # Course handicap por tee seleccionado
            if T["tee"]:
                tee = T["tee"]
                par18 = tee["par"] if tee["par"] >= 60 else tee["par"] * 2
                pareja["ch1"] = course_handicap(float(hi1), tee["slope"], tee["rating"], par18 // 2)
                pareja["ch2"] = course_handicap(float(hi2), tee["slope"], tee["rating"], par18 // 2)
                st.caption(f"Course Handicap → {pareja['j1']['name']}: **{pareja['ch1']}** | {pareja['j2']['name']}: **{pareja['ch2']}**")



    st.divider()
    if st.button("🚀 Iniciar Torneo", type="primary"):
        # Validar que no haya jugadores repetidos
        all_players_selected = []
        valid = True
        for p in T["parejas"]:
            if p["j1"] and p["j2"]:
                if p["j1"]["id"] == p["j2"]["id"]:
                    st.error(f"❌ {p['nombre']}: los dos jugadores son el mismo.")
                    valid = False
                all_players_selected.extend([p["j1"]["id"], p["j2"]["id"]])

        if len(all_players_selected) != len(set(all_players_selected)):
            st.error("❌ Hay jugadores repetidos entre parejas.")
            valid = False

        if valid:
            # Inicializar scores vacíos
            for p in T["parejas"]:
                T["scores"][p["id"]] = {h["hole_number"]: {"j1": None, "j2": None} for h in holes}
            T["iniciado"] = True
            st.success("✅ Torneo iniciado. Ve a la pestaña Scores.")
            st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# TAB 2: SCORES
# ══════════════════════════════════════════════════════════════════════════════
with tab_scores:
    if not T["iniciado"]:
        st.info("⚠️ Primero configura e inicia el torneo en la pestaña Configuración.")
    else:
        holes = get_holes()
        tee = T["tee"]
        par18 = tee["par"] if tee["par"] >= 60 else tee["par"] * 2

        # Calcular strokes given para todos los jugadores
        strokes_map = {}
        for p in T["parejas"]:
            strokes_map[p["id"]] = {
                "j1": strokes_given(p["ch1"], holes),
                "j2": strokes_given(p["ch2"], holes),
            }

        st.header("📝 Captura de Scores")
        st.caption(f"Tee: {tee['color']} | Rating: {tee['rating']} | Slope: {tee['slope']}")

        # Selector de hoyo
        hole_num = st.select_slider("Hoyo", options=list(range(1, 19)), value=1)
        hole_info = next(h for h in holes if h["hole_number"] == hole_num)

        st.markdown(f"### Hoyo {hole_num} — Par {hole_info['par']} | HCP Hoyo: {hole_info['handicap']}")

        cols = st.columns(len(T["parejas"]))
        for i, pareja in enumerate(T["parejas"]):
            with cols[i]:
                st.markdown(f"**{pareja['nombre']}**")
                sg1 = strokes_map[pareja["id"]]["j1"][hole_num]
                sg2 = strokes_map[pareja["id"]]["j2"][hole_num]

                st.caption(f"{pareja['j1']['name']} (+{sg1} golpes)")
                g1 = st.number_input(
                    f"Golpes {pareja['j1']['name']}",
                    min_value=1, max_value=15,
                    value=T["scores"][pareja["id"]][hole_num]["j1"] or hole_info["par"],
                    key=f"g1_{pareja['id']}_{hole_num}"
                )
                T["scores"][pareja["id"]][hole_num]["j1"] = g1

                st.caption(f"{pareja['j2']['name']} (+{sg2} golpes)")
                g2 = st.number_input(
                    f"Golpes {pareja['j2']['name']}",
                    min_value=1, max_value=15,
                    value=T["scores"][pareja["id"]][hole_num]["j2"] or hole_info["par"],
                    key=f"g2_{pareja['id']}_{hole_num}"
                )
                T["scores"][pareja["id"]][hole_num]["j2"] = g2

                net1 = net_score(g1, sg1)
                net2 = net_score(g2, sg2)
                bola_baja = min(net1, net2)
                st.metric("🏌️ Bola baja neta", bola_baja, delta=f"{bola_baja - hole_info['par']} vs par")

        # Mini scoreboard en vivo
        st.divider()
        st.subheader("📊 Scoreboard en vivo")
        live_data = []
        for pareja in T["parejas"]:
            total_net = 0
            hoyos_jugados = 0
            for h in holes:
                hn = h["hole_number"]
                s = T["scores"][pareja["id"]][hn]
                if s["j1"] is not None and s["j2"] is not None:
                    sg1 = strokes_map[pareja["id"]]["j1"][hn]
                    sg2 = strokes_map[pareja["id"]]["j2"][hn]
                    n1 = net_score(s["j1"], sg1)
                    n2 = net_score(s["j2"], sg2)
                    total_net += min(n1, n2)
                    hoyos_jugados += 1

            par_jugado = sum(h["par"] for h in holes if h["hole_number"] <= hoyos_jugados)
            vs_par = total_net - par_jugado if hoyos_jugados > 0 else 0
            live_data.append({
                "Pareja": pareja["nombre"],
                "Jugadores": f"{pareja['j1']['name']} / {pareja['j2']['name']}",
                "Hoyos": hoyos_jugados,
                "Total Neto": total_net if hoyos_jugados > 0 else "-",
                "vs Par": f"{'+' if vs_par > 0 else ''}{vs_par}" if hoyos_jugados > 0 else "-",
            })

        live_df = pd.DataFrame(live_data).sort_values("Total Neto")
        st.dataframe(live_df, use_container_width=True, hide_index=True)

# ══════════════════════════════════════════════════════════════════════════════
# TAB 3: RESULTADOS FINALES
# ══════════════════════════════════════════════════════════════════════════════
with tab_resultados:
    if not T["iniciado"]:
        st.info("⚠️ Primero inicia el torneo.")
    else:
        holes = get_holes()
        tee = T["tee"]

        st.header("🏆 Resultados Finales")

        # Tabla hoyo por hoyo
        strokes_map = {}
        for p in T["parejas"]:
            strokes_map[p["id"]] = {
                "j1": strokes_given(p["ch1"], holes),
                "j2": strokes_given(p["ch2"], holes),
            }

        result_data = []
        scorecard_rows = []

        for pareja in T["parejas"]:
            row = {"Pareja": pareja["nombre"]}
            total_bruto = 0
            total_neto = 0
            for h in holes:
                hn = h["hole_number"]
                s = T["scores"][pareja["id"]][hn]
                if s["j1"] is not None and s["j2"] is not None:
                    sg1 = strokes_map[pareja["id"]]["j1"][hn]
                    sg2 = strokes_map[pareja["id"]]["j2"][hn]
                    n1 = net_score(s["j1"], sg1)
                    n2 = net_score(s["j2"], sg2)
                    bb = min(n1, n2)
                    row[f"H{hn}"] = bb
                    total_neto += bb
                    total_bruto += min(s["j1"], s["j2"])
                else:
                    row[f"H{hn}"] = "-"
            row["Total Bruto"] = total_bruto
            row["Total Neto"] = total_neto
            par_total = sum(h["par"] for h in holes)
            row["vs Par"] = f"{'+' if (total_neto - par_total) > 0 else ''}{total_neto - par_total}"
            scorecard_rows.append(row)
            result_data.append({
                "Pos": 0,
                "Pareja": pareja["nombre"],
                "Jugadores": f"{pareja['j1']['name']} / {pareja['j2']['name']}",
                "HCP1": pareja["ch1"],
                "HCP2": pareja["ch2"],
                "Total Neto": total_neto,
                "vs Par": row["vs Par"],
            })

        # Ordenar y asignar posición
        result_data.sort(key=lambda x: x["Total Neto"])
        for i, r in enumerate(result_data):
            r["Pos"] = i + 1

        # Ganador
        if result_data:
            ganador = result_data[0]
            st.success(f"🥇 **Ganador: {ganador['Pareja']}** ({ganador['Jugadores']}) — {ganador['vs Par']} vs par")

        st.subheader("Clasificación Final")
        st.dataframe(pd.DataFrame(result_data), use_container_width=True, hide_index=True)

        st.subheader("Scorecard Detallado (Bola Baja Neta)")
        # Header con par y handicap
        par_row = {"Pareja": "Par"}
        hcp_row = {"Pareja": "HCP Hoyo"}
        for h in holes:
            par_row[f"H{h['hole_number']}"] = h["par"]
            hcp_row[f"H{h['hole_number']}"] = h["handicap"]
        par_row["Total Bruto"] = sum(h["par"] for h in holes)
        par_row["Total Neto"] = sum(h["par"] for h in holes)
        par_row["vs Par"] = "E"
        hcp_row["Total Bruto"] = ""
        hcp_row["Total Neto"] = ""
        hcp_row["vs Par"] = ""

        scorecard_df = pd.DataFrame([par_row, hcp_row] + scorecard_rows)
        st.dataframe(scorecard_df, use_container_width=True, hide_index=True)

        st.divider()
        if st.button("🔄 Nuevo Torneo", type="secondary"):
            st.session_state.torneo = {
                "parejas": [],
                "scores": {},
                "tee": None,
                "fecha": date.today(),
                "iniciado": False,
            }
            st.rerun()
