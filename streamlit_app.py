import streamlit as st
import pandas as pd
from supabase import create_client, Client
from datetime import date
import random
import string
import time
from collections import defaultdict

st.set_page_config(page_title="Bola Baja por Parejas", layout="wide")

# ─── SUPABASE ─────────────────────────────────────────────────────────────────
@st.cache_resource
def get_supabase() -> Client:
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

supabase = get_supabase()

# ─── HELPERS ──────────────────────────────────────────────────────────────────
def get_holes():
    res = supabase.table("holes").select("hole_number, par, handicap").order("hole_number").execute()
    return sorted(res.data, key=lambda x: x["hole_number"])

def gen_code(prefix="LC"):
    return f"{prefix}-" + "".join(random.choices(string.digits, k=4))

def course_handicap(hi, slope, rating, par):
    return round(float(hi or 0) * (slope / 113) + (rating - par))

def strokes_given(player_ch, holes):
    result = {}
    for h in holes:
        extras = 0
        if player_ch >= h["handicap"]: extras = 1
        if player_ch >= 18 + h["handicap"]: extras = 2
        if player_ch >= 36 + h["handicap"]: extras = 3
        result[h["hole_number"]] = extras
    return result

def get_active_tournaments():
    return supabase.table("tournaments").select("id, name, date, access_code, tee_id").order("date", desc=True).limit(20).execute().data

def get_tournament_by_code(code):
    res = supabase.table("tournaments").select("*").eq("access_code", code.upper()).execute()
    return res.data[0] if res.data else None

def get_group_by_code(code):
    res = supabase.table("groups").select("*").eq("access_code", code.upper()).execute()
    return res.data[0] if res.data else None

def get_groups_for_tournament(tournament_id):
    return supabase.table("groups").select("*").eq("tournament_id", tournament_id).execute().data

def get_group_players(group_id):
    return supabase.table("group_players").select("*").eq("group_id", group_id).order("pair_order").execute().data

def get_all_scores(tournament_id):
    return supabase.table("tournament_scores").select("*").eq("tournament_id", tournament_id).execute().data

def get_group_scores(tournament_id, group_id):
    return supabase.table("tournament_scores").select("*").eq("tournament_id", tournament_id).eq("group_id", group_id).execute().data

def upsert_score(tournament_id, group_id, pair_name, player_id, guest_id, hole_number, strokes, net_strokes):
    null_uuid = "00000000-0000-0000-0000-000000000000"
    existing = supabase.table("tournament_scores") \
        .select("id") \
        .eq("tournament_id", tournament_id) \
        .eq("group_id", group_id) \
        .eq("pair_name", pair_name) \
        .eq("hole_number", hole_number) \
        .eq("player_id", player_id if player_id else null_uuid) \
        .execute()
    data = {
        "tournament_id": tournament_id,
        "group_id": group_id,
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

def build_strokes_map(parejas_agrupadas, holes):
    result = {}
    for pair_name, jugadores in parejas_agrupadas.items():
        result[pair_name] = {
            "j1": strokes_given(jugadores[0]["course_handicap"] or 0, holes),
            "j2": strokes_given(jugadores[1]["course_handicap"] or 0, holes),
        }
    return result

def agrupar_parejas(rows):
    """Convierte lista de filas (1 por jugador) en dict por pair_name con j1/j2."""
    grupos = {}
    for r in rows:
        pn = r["pair_name"]
        if pn not in grupos:
            grupos[pn] = []
        grupos[pn].append(r)
    # Ordenar por pair_order dentro de cada pareja
    for pn in grupos:
        grupos[pn].sort(key=lambda x: x.get("pair_order", 0))
    return grupos

def save_guest(name, hi, fecha, player_id=None):
    data = {"name": name, "handicap_index": float(hi or 0), "tournament_date": str(fecha)}
    if player_id:
        data["player_id"] = player_id
    res = supabase.table("guests").insert(data).execute()
    return res.data[0]["id"]

# ─── SESSION STATE ─────────────────────────────────────────────────────────────
for key, default in [
    ("screen", "home"),
    ("role", None),
    ("tournament", None),
    ("group", None),
    ("parejas", []),
    ("strokes_map", {}),
    ("admin_authed", False),
    ("show_admin_login", False),
]:
    if key not in st.session_state:
        st.session_state[key] = default

def go_home():
    st.session_state.screen = "home"
    st.session_state.role = None
    st.session_state.tournament = None
    st.session_state.group = None
    st.session_state.parejas = []
    st.session_state.strokes_map = {}

# ══════════════════════════════════════════════════════════════════════════════
# PANTALLA HOME
# ══════════════════════════════════════════════════════════════════════════════
if st.session_state.screen == "home":
    st.title("Bola Baja por Parejas - Las Cruces")
    st.markdown("---")

    col_org, col_lider, col_spec = st.columns(3)

    # ── ADMINISTRADOR ──────────────────────────────────────────────────────
    with col_org:
        st.subheader("Administrador")

        if not st.session_state.admin_authed:
            if st.button("Crear Torneo", type="primary"):
                st.session_state.show_admin_login = True

            if st.session_state.show_admin_login:
                st.caption("Ingresa tus credenciales")
                admin_email = st.text_input("Email", key="admin_email")
                admin_pass = st.text_input("Password", type="password", key="admin_pass")
                if st.button("Iniciar sesion", key="admin_login_btn"):
                    try:
                        from supabase import create_client as _cc
                        _tmp = _cc(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
                        auth_res = _tmp.auth.sign_in_with_password({"email": admin_email, "password": admin_pass})
                        if auth_res.user:
                            st.session_state.admin_authed = True
                            st.session_state.show_admin_login = False
                            st.rerun()
                        else:
                            st.error("Credenciales incorrectas.")
                    except Exception as e:
                        st.error(f"Error: {e}")
        else:
            st.caption("Sesion admin activa")
            if st.button("Cerrar sesion", key="admin_logout"):
                st.session_state.admin_authed = False
                st.rerun()

            st.markdown("**Crear nuevo torneo**")
            tees = supabase.table("tees").select("id, name, color, rating, slope, par").execute().data
            if not tees:
                st.error("No se pudieron cargar los tees.")
            else:
                tee_opts = {f"{t['color']} - Rating {t['rating']} / Slope {t['slope']}": t for t in tees}
                with st.form("form_org"):
                    fecha = st.date_input("Fecha", value=date.today())
                    tee_label = st.selectbox("Tee", list(tee_opts.keys()))
                    submitted = st.form_submit_button("Crear Torneo", type="primary")

                if submitted:
                    tee = tee_opts[tee_label]
                    code = gen_code("LC")
                    supabase.table("tournaments").insert({
                        "name": f"Bola Baja - {fecha}",
                        "date": str(fecha),
                        "tee_id": tee["id"],
                        "format": "bola_baja_parejas",
                        "access_code": code,
                    }).execute()
                    st.success("Torneo creado")
                    st.info(f"Codigo maestro: {code}\nComparte este codigo con los lideres de grupo.")

            st.markdown("---")
            st.markdown("**Borrar torneo**")
            torneos_admin = get_active_tournaments()
            if torneos_admin:
                del_opts = {f"{t['name']} ({t['access_code']})": t for t in torneos_admin}
                del_sel = st.selectbox("Torneo a borrar", list(del_opts.keys()), key="del_sel")
                if st.button("Borrar torneo", type="secondary"):
                    t_del = del_opts[del_sel]
                    grupos = supabase.table("groups").select("id").eq("tournament_id", t_del["id"]).execute().data
                    for g in grupos:
                        supabase.table("group_players").delete().eq("group_id", g["id"]).execute()
                    supabase.table("tournament_scores").delete().eq("tournament_id", t_del["id"]).execute()
                    supabase.table("groups").delete().eq("tournament_id", t_del["id"]).execute()
                    supabase.table("tournament_pairs").delete().eq("tournament_id", t_del["id"]).execute()
                    supabase.table("tournaments").delete().eq("id", t_del["id"]).execute()
                    st.success(f"Torneo {t_del['name']} borrado.")
                    st.rerun()
            else:
                st.info("No hay torneos para borrar.")

    # ── LIDER DE GRUPO ─────────────────────────────────────────────────────
    with col_lider:
        st.subheader("Lider de Grupo")
        st.caption("Entra con el codigo maestro del torneo")

        master_code = st.text_input("Codigo maestro (ej. LC-1234)", key="master_code")
        if st.button("Entrar como Lider", type="primary"):
            t = get_tournament_by_code(master_code)
            if t:
                tee_res = supabase.table("tees").select("*").eq("id", t["tee_id"]).execute()
                st.session_state.tournament = {**t, "tee": tee_res.data[0]}
                st.session_state.role = "leader"
                st.session_state.screen = "leader_setup"
                st.rerun()
            else:
                st.error("Codigo no encontrado.")

        st.markdown("---")
        st.caption("Ya tienes un grupo creado?")
        group_code = st.text_input("Codigo de grupo (ej. GR-1234)", key="group_code_input")
        if st.button("Continuar mi grupo"):
            g = get_group_by_code(group_code)
            if g:
                t_res = supabase.table("tournaments").select("*").eq("id", g["tournament_id"]).execute()
                t = t_res.data[0]
                tee_res = supabase.table("tees").select("*").eq("id", t["tee_id"]).execute()
                holes = get_holes()
                rows = get_group_players(g["id"])
                parejas_agrupadas = agrupar_parejas(rows)
                st.session_state.tournament = {**t, "tee": tee_res.data[0]}
                st.session_state.group = g
                st.session_state.parejas = parejas_agrupadas
                st.session_state.strokes_map = build_strokes_map(parejas_agrupadas, holes)
                st.session_state.role = "leader"
                st.session_state.screen = "scores"
                st.rerun()
            else:
                st.error("Codigo de grupo no encontrado.")

    # ── ESPECTADOR ─────────────────────────────────────────────────────────
    with col_spec:
        st.subheader("Espectador")
        st.caption("Ve el leaderboard general")
        torneos = get_active_tournaments()
        if torneos:
            t_opts = {t["name"]: t for t in torneos}
            sel = st.selectbox("Torneo", list(t_opts.keys()), key="spec_sel")
            if st.button("Ver Leaderboard", type="primary"):
                t = t_opts[sel]
                tee_res = supabase.table("tees").select("*").eq("id", t["tee_id"]).execute()
                st.session_state.tournament = {**t, "tee": tee_res.data[0]}
                st.session_state.role = "spectator"
                st.session_state.screen = "leaderboard"
                st.rerun()
        else:
            st.info("No hay torneos activos.")

# ══════════════════════════════════════════════════════════════════════════════
# PANTALLA LEADER SETUP
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state.screen == "leader_setup":
    t = st.session_state.tournament
    tee = t["tee"]

    players = supabase.table("players").select("id, name, current_handicap").order("name").execute().data

    st.title(f"Bola Baja - {t['name']}")
    st.caption(f"Tee: {tee['color']} | Rating: {tee['rating']} | Slope: {tee['slope']}")
    if st.button("Salir"):
        go_home()
        st.rerun()
    st.markdown("---")
    st.subheader("Crear tu Grupo")

    player_opts = {"-- Seleccione jugador --": None}
    player_opts.update({
        f"{p['name']} (HI: {int(p['current_handicap']) if p['current_handicap'] else 'N/A'})": p
        for p in players
    })
    player_labels = list(player_opts.keys())

    group_name = st.text_input("Nombre del grupo", value="Grupo 1")
    num_parejas = st.number_input("Numero de parejas", min_value=1, max_value=8, value=2)

    parejas_setup = []
    valid = True

    for i in range(int(num_parejas)):
        st.markdown(f"**Pareja {i+1}**")
        pair_name = st.text_input("Nombre de la pareja", value=f"Pareja {i+1}", key=f"pname_{i}")

        col_j1, col_j2 = st.columns(2)

        # Jugador 1
        with col_j1:
            st.caption("Jugador 1")
            guest1 = st.checkbox("Invitado", key=f"g1_{i}")
            if guest1:
                j1_name = st.text_input("Nombre", key=f"j1gn_{i}")
                hi1 = st.number_input("HI", min_value=0.0, max_value=54.0, value=0.0, step=0.1, key=f"j1gh_{i}")
                j1 = {"id": None, "name": j1_name or "Invitado 1", "current_handicap": hi1, "_is_guest": True}
            else:
                j1_label = st.selectbox("Jugador 1", player_labels, key=f"j1_{i}")
                j1_data = player_opts.get(j1_label)
                j1 = dict(j1_data) if j1_data else None
                if j1 is not None and not j1.get("current_handicap"):
                    hi1 = st.number_input("HI manual", min_value=0.0, max_value=54.0, value=0.0, step=0.1, key=f"hi1_{i}")
                    j1["current_handicap"] = hi1
                    j1["_hi_temporal"] = True

        # Jugador 2
        with col_j2:
            st.caption("Jugador 2")
            guest2 = st.checkbox("Invitado", key=f"g2_{i}")
            if guest2:
                j2_name = st.text_input("Nombre", key=f"j2gn_{i}")
                hi2 = st.number_input("HI", min_value=0.0, max_value=54.0, value=0.0, step=0.1, key=f"j2gh_{i}")
                j2 = {"id": None, "name": j2_name or "Invitado 2", "current_handicap": hi2, "_is_guest": True}
            else:
                j2_label = st.selectbox("Jugador 2", player_labels, key=f"j2_{i}")
                j2_data = player_opts.get(j2_label)
                j2 = dict(j2_data) if j2_data else None
                if j2 is not None and not j2.get("current_handicap"):
                    hi2 = st.number_input("HI manual", min_value=0.0, max_value=54.0, value=0.0, step=0.1, key=f"hi2_{i}")
                    j2["current_handicap"] = hi2
                    j2["_hi_temporal"] = True

        if j1 is not None and j2 is not None:
            ch1 = int(j1.get("current_handicap") or 0)
            ch2 = int(j2.get("current_handicap") or 0)
            parejas_setup.append({"pair_name": pair_name, "j1": j1, "j2": j2, "player1_ch": ch1, "player2_ch": ch2})
        else:
            st.warning(f"Selecciona ambos jugadores para la Pareja {i+1}")
            valid = False

        st.divider()

    if st.button("Confirmar Grupo e Iniciar", type="primary"):
        if not valid or len(parejas_setup) != int(num_parejas):
            st.error("Completa todos los jugadores antes de continuar.")
        else:
            holes = get_holes()
            group_code = gen_code("GR")
            g_res = supabase.table("groups").insert({
                "tournament_id": t["id"],
                "name": group_name,
                "access_code": group_code,
            }).execute()
            group_id = g_res.data[0]["id"]

            parejas_db_rows = []
            for idx, p in enumerate(parejas_setup):
                for jnum, jkey in enumerate(["j1", "j2"]):
                    j = p[jkey]
                    guest_db_id = None
                    if j.get("_is_guest"):
                        guest_db_id = save_guest(j["name"], j["current_handicap"], t["date"])
                    elif j.get("_hi_temporal"):
                        guest_db_id = save_guest(j["name"], j["current_handicap"], t["date"], player_id=j["id"])
                    ch = p["player1_ch"] if jkey == "j1" else p["player2_ch"]
                    row = supabase.table("group_players").insert({
                        "group_id": group_id,
                        "pair_order": (idx * 2) + jnum + 1,
                        "pair_name": p["pair_name"],
                        "player_id": None if (j.get("_is_guest") or j.get("_hi_temporal")) else j["id"],
                        "guest_id": guest_db_id,
                        "player_name": j["name"],
                        "course_handicap": ch,
                    }).execute()
                    parejas_db_rows.append(row.data[0])

            parejas_agrupadas = agrupar_parejas(parejas_db_rows)
            st.session_state.group = g_res.data[0]
            st.session_state.parejas = parejas_agrupadas
            st.session_state.strokes_map = build_strokes_map(parejas_agrupadas, holes)
            st.session_state.screen = "scores"
            st.success("Grupo creado")
            st.info(f"Codigo de grupo: {group_code}\nGuardalo para poder volver a entrar.")
            time.sleep(2)
            st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# PANTALLA SCORES
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state.screen == "scores":
    t = st.session_state.tournament
    g = st.session_state.group
    tee = t["tee"]
    parejas = st.session_state.parejas
    strokes_map = st.session_state.strokes_map
    holes = get_holes()

    st.title(f"{t['name']} - {g['name']}")
    st.caption(f"Tee: {tee['color']} | Rating: {tee['rating']} | Slope: {tee['slope']} | Codigo grupo: {g['access_code']}")

    col_back, col_lb = st.columns(2)
    with col_back:
        if st.button("Salir"):
            go_home()
            st.rerun()
    with col_lb:
        if st.button("Ver Leaderboard General"):
            st.session_state.screen = "leaderboard"
            st.rerun()

    st.markdown("---")

    existing_scores = get_group_scores(t["id"], g["id"])
    existing_map = {}
    for s in existing_scores:
        existing_map[(s["pair_name"], s["hole_number"], s["player_id"], s["guest_id"])] = s["strokes"]

    # Hoyos con scores guardados (al menos una entrada)
    hoyos_con_scores = set(s["hole_number"] for s in existing_scores)

    # Selector de hoyo - HTML grid clickeable
    if "hole_num" not in st.session_state:
        st.session_state.hole_num = 1

    # Leer si viene un hoyo seleccionado via query params
    qp = st.query_params
    if "h" in qp:
        try:
            st.session_state.hole_num = int(qp["h"])
        except:
            pass

    st.markdown("**Selecciona hoyo:**")
    cells = ""
    for h in range(1, 19):
        tiene = h in hoyos_con_scores
        bg = "#2e7d32" if tiene else "#555"
        selected = "border:3px solid #FFD700;" if h == st.session_state.hole_num else "border:3px solid transparent;"
        cells += f'<a href="?h={h}" style="text-decoration:none;"><div style="background:{bg};{selected}color:white;border-radius:8px;padding:10px 0;text-align:center;font-weight:bold;font-size:15px;">{h}</div></a>'

    st.markdown(f"""
    <div style="display:grid;grid-template-columns:repeat(9,1fr);gap:5px;margin-bottom:10px;">
    {cells}
    </div>
    """, unsafe_allow_html=True)

    hole_num = st.session_state.hole_num
    hole_info_sel = next(h for h in holes if h["hole_number"] == hole_num)
    st.markdown(f"**Hoyo {hole_num} — Par {hole_info_sel['par']} | HCP: {hole_info_sel['handicap']}**")
    hole_info = hole_info_sel

    hole_info = next(h for h in holes if h["hole_number"] == hole_num)
    st.markdown(f"### Hoyo {hole_num} — Par {hole_info['par']} | HCP Hoyo: {hole_info['handicap']}")

    cols = st.columns(max(len(parejas), 1))
    scores_to_save = []

    for i, (pair_name, jugadores) in enumerate(parejas.items()):
        j1 = jugadores[0]
        j2 = jugadores[1]
        with cols[i]:
            st.markdown(f"**{pair_name}**")
            sg1 = strokes_map[pair_name]["j1"][hole_num]
            sg2 = strokes_map[pair_name]["j2"][hole_num]

            pid1 = j1.get("player_id")
            gid1 = j1.get("guest_id")
            pid2 = j2.get("player_id")
            gid2 = j2.get("guest_id")

            prev1 = existing_map.get((pair_name, hole_num, pid1, gid1), hole_info["par"])
            prev2 = existing_map.get((pair_name, hole_num, pid2, gid2), hole_info["par"])
            saved1 = (pair_name, hole_num, pid1, gid1) in existing_map
            saved2 = (pair_name, hole_num, pid2, gid2) in existing_map

            st.caption(f"{j1['player_name']} | +{sg1} ventaja{'  ✅' if saved1 else ''}")
            g1_val = st.number_input(f"Golpes {j1['player_name']}", min_value=1, max_value=15,
                value=prev1, key=f"g1_{pair_name}_{hole_num}")

            st.caption(f"{j2['player_name']} | +{sg2} ventaja{'  ✅' if saved2 else ''}")
            g2_val = st.number_input(f"Golpes {j2['player_name']}", min_value=1, max_value=15,
                value=prev2, key=f"g2_{pair_name}_{hole_num}")

            net1 = g1_val - sg1
            net2 = g2_val - sg2
            bola_baja = min(net1, net2)
            ganador_hoyo = j1["player_name"] if net1 <= net2 else j2["player_name"]
            st.metric("Bola baja neta", bola_baja, delta=f"{bola_baja - hole_info['par']} vs par")
            st.caption(f"Bola baja: {ganador_hoyo}")

            scores_to_save.append((pair_name, pid1, gid1, g1_val, net1))
            scores_to_save.append((pair_name, pid2, gid2, g2_val, net2))

    if st.button(f"Guardar Hoyo {hole_num}", type="primary"):
        for pair_name, pid, gid, strokes, net in scores_to_save:
            upsert_score(t["id"], g["id"], pair_name, pid, gid, hole_num, strokes, net)
        st.success(f"Hoyo {hole_num} guardado.")
        st.rerun()

    st.markdown("---")
    st.subheader("Scoreboard de tu grupo")
    scores_db = get_group_scores(t["id"], g["id"])
    hole_scores = defaultdict(list)
    for s in scores_db:
        hole_scores[(s["pair_name"], s["hole_number"])].append(s["net_strokes"])

    group_board = []
    for pair_name, jugadores in parejas.items():
        j1 = jugadores[0]
        j2 = jugadores[1]
        total = 0
        hoyos = set()
        for (pn, hn), nets in hole_scores.items():
            if pn == pair_name and len(nets) >= 2:
                total += min(nets)
                hoyos.add(hn)
        par_jugado = sum(h["par"] for h in holes if h["hole_number"] in hoyos)
        vs_par = total - par_jugado if hoyos else 0
        group_board.append({
            "Pareja": pair_name,
            "Jugadores": f"{j1['player_name']} / {j2['player_name']}",
            "Hoyos": f"{len(hoyos)}/18",
            "Total Neto": total if hoyos else "-",
            "vs Par": f"{'+' if vs_par > 0 else ''}{vs_par}" if hoyos else "-",
        })

    group_board.sort(key=lambda x: x["Total Neto"] if isinstance(x["Total Neto"], int) else 999)
    st.dataframe(pd.DataFrame(group_board), use_container_width=True, hide_index=True)

# ══════════════════════════════════════════════════════════════════════════════
# PANTALLA LEADERBOARD GENERAL
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state.screen == "leaderboard":
    t = st.session_state.tournament
    tee = t["tee"]
    holes = get_holes()

    st.title(f"Leaderboard - {t['name']}")
    st.caption(f"Tee: {tee['color']} | Rating: {tee['rating']} | Slope: {tee['slope']}")

    col_back, col_refresh = st.columns(2)
    with col_back:
        if st.button("Salir"):
            go_home()
            st.rerun()
    with col_refresh:
        if st.button("Actualizar"):
            st.rerun()

    st.markdown("---")

    groups = get_groups_for_tournament(t["id"])
    all_scores = get_all_scores(t["id"])

    hole_scores = defaultdict(list)
    for s in all_scores:
        hole_scores[(s["pair_name"], s["group_id"], s["hole_number"])].append(s["net_strokes"])

    leader_data = []
    for grp in groups:
        rows = get_group_players(grp["id"])
        parejas_grp = agrupar_parejas(rows)
        for pair_name, jugadores in parejas_grp.items():
            j1 = jugadores[0]
            j2 = jugadores[1]
            total = 0
            hoyos = set()
            for (pn, gid, hn), nets in hole_scores.items():
                if pn == pair_name and gid == grp["id"] and len(nets) >= 2:
                    total += min(nets)
                    hoyos.add(hn)
            par_jugado = sum(h["par"] for h in holes if h["hole_number"] in hoyos)
            vs_par = total - par_jugado if hoyos else 0
            leader_data.append({
                "Pos": 0,
                "Grupo": grp["name"],
                "Pareja": pair_name,
                "Jugadores": f"{j1['player_name']} / {j2['player_name']}",
                "HCP": f"{j1['course_handicap']} / {j2['course_handicap']}",
                "Hoyos": f"{len(hoyos)}/18",
                "Total Neto": total if hoyos else 9999,
                "vs Par": f"{'+' if vs_par > 0 else ''}{vs_par}" if hoyos else "-",
            })

    leader_data.sort(key=lambda x: x["Total Neto"])
    for i, r in enumerate(leader_data):
        r["Pos"] = i + 1
        if r["Total Neto"] == 9999:
            r["Total Neto"] = "-"

    if leader_data and leader_data[0]["Total Neto"] != "-":
        lider = leader_data[0]
        st.success(f"Lider: {lider['Pareja']} ({lider['Jugadores']}) - {lider['vs Par']} | Grupo: {lider['Grupo']}")

    st.dataframe(
        pd.DataFrame(leader_data)[["Pos", "Grupo", "Pareja", "Jugadores", "HCP", "Hoyos", "Total Neto", "vs Par"]],
        use_container_width=True, hide_index=True
    )

    if st.session_state.role == "spectator":
        st.caption("Se actualiza cada 30 segundos")
        time.sleep(30)
        st.rerun()
