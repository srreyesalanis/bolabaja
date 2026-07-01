import streamlit as st
import pandas as pd
from supabase import create_client, Client
from datetime import date
import random
import string
import time
from collections import defaultdict

st.set_page_config(page_title="Bola Baja por Parejas", layout="wide")

# SUPABASE
@st.cache_resource
def get_supabase() -> Client:
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

supabase = get_supabase()

# HELPERS
def get_holes():
    res = supabase.table("holes").select("hole_number, par, handicap").order("hole_number").execute()
    return sorted(res.data, key=lambda x: x["hole_number"])

def gen_code(prefix="LC"):
    return f"{prefix}-" + "".join(random.choices(string.digits, k=4))

def gen_group_code():
    return "".join(random.choices(string.digits, k=6))

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
    res = supabase.table("groups").select("*").eq("access_code", code.strip()).execute()
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
    grupos = {}
    for r in rows:
        pn = r["pair_name"]
        if pn not in grupos:
            grupos[pn] = []
        grupos[pn].append(r)
    for pn in grupos:
        grupos[pn].sort(key=lambda x: x.get("pair_order", 0))
    return grupos

def save_guest(name, hi, fecha, tournament_id, player_id=None):
    data = {"name": name, "handicap_index": float(hi or 0), "tournament_date": str(fecha), "tournament_id": tournament_id}
    if player_id:
        data["player_id"] = player_id
    res = supabase.table("guests").insert(data).execute()
    return res.data[0]["id"]

def fmt_score(val, par, hoyos):
    if not hoyos:
        return "-"
    diff = val - par
    sign = "+" if diff > 0 else ""
    return f"{val} ({sign}{diff})"

# SESSION STATE
for key, default in [
    ("screen", "home"),
    ("role", None),
    ("tournament", None),
    ("group", None),
    ("parejas", {}),
    ("strokes_map", {}),
    ("admin_authed", False),
    ("show_admin_login", False),
    ("hole_num", 1),
    ("admin_new_tournament_code", ""),
]:
    if key not in st.session_state:
        st.session_state[key] = default

def go_home():
    st.session_state.screen = "home"
    st.session_state.role = None
    st.session_state.tournament = None
    st.session_state.group = None
    st.session_state.parejas = {}
    st.session_state.strokes_map = {}
    st.session_state.hole_num = 1
    st.query_params.clear()

# ==============================================================================
# AUTO-RESTORE desde query_params (persiste al cerrar/reabrir en movil)
# Si la URL tiene ?g=CODIGO&h=HOYO, restauramos el grupo automaticamente
# ==============================================================================
_qp_group = st.query_params.get("g")
_qp_hole = st.query_params.get("h")
if _qp_group and st.session_state.screen == "home" and not st.session_state.group:
    _g = get_group_by_code(_qp_group)
    if _g:
        _t_res = supabase.table("tournaments").select("*").eq("id", _g["tournament_id"]).execute()
        _t = _t_res.data[0]
        _tee_res = supabase.table("tees").select("*").eq("id", _t["tee_id"]).execute()
        _holes = get_holes()
        _rows = get_group_players(_g["id"])
        _parejas = agrupar_parejas(_rows)
        st.session_state.tournament = {**_t, "tee": _tee_res.data[0]}
        st.session_state.group = _g
        st.session_state.parejas = _parejas
        st.session_state.strokes_map = build_strokes_map(_parejas, _holes)
        st.session_state.role = "leader"
        st.session_state.screen = "scores"
        if _qp_hole:
            try:
                st.session_state.hole_num = int(_qp_hole)
            except Exception:
                pass
        st.rerun()

# ==============================================================================
# ROUTER - una sola pantalla a la vez
# ==============================================================================
_screen = st.session_state.screen

# Prevenir render fantasma: si el screen cambio en este ciclo, limpiar y rerenderizar
if "_last_screen" not in st.session_state:
    st.session_state._last_screen = _screen
elif st.session_state._last_screen != _screen:
    st.session_state._last_screen = _screen
    st.rerun()

if _screen == "home":
    st.title("Bola Baja por Parejas - Las Cruces")
    st.markdown("---")
    col_org, col_spec = st.columns(2)

    with col_org:
        st.subheader("Administrador")
        if not st.session_state.admin_authed:
            if st.button("Administrar Torneo", type="primary"):
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

            with st.container(border=True):
                st.markdown('<div style="background:rgba(30,100,70,0.18);margin:-12px -12px 8px -12px;padding:6px 14px;border-radius:6px 6px 0 0;"><strong>Crear nuevo torneo</strong></div>', unsafe_allow_html=True)
                tees = supabase.table("tees").select("id, name, color, rating, slope, par").execute().data
                if not tees:
                    st.error("No se pudieron cargar los tees.")
                else:
                    tee_opts = {f"{t['color']} - Rating {t['rating']} / Slope {t['slope']}": t for t in tees}
                    fecha = st.date_input("Fecha", value=date.today(), key="admin_fecha")
                    tee_label = st.selectbox("Tee", list(tee_opts.keys()), key="admin_tee")
                    if st.button("Crear Torneo", type="primary", key="admin_crear_torneo"):
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
                        st.info(f"Codigo maestro: {code}")
                        st.session_state["admin_new_tournament_code"] = code
            with st.container(border=True):
                st.markdown('<div style="background:rgba(20,70,130,0.18);margin:-12px -12px 8px -12px;padding:6px 14px;border-radius:6px 6px 0 0;"><strong>Crear Grupo</strong></div>', unsafe_allow_html=True)
                torneos_lider = get_active_tournaments()
                if torneos_lider:
                    lider_opts = {t["name"]: t for t in torneos_lider}
                    lider_sel = st.selectbox("Selecciona torneo", list(lider_opts.keys()), key="lider_torneo_sel")
                    if st.button("Crear Grupo", key="admin_enter_leader", type="primary"):
                        t_found = lider_opts[lider_sel]
                        tee_res = supabase.table("tees").select("*").eq("id", t_found["tee_id"]).execute()
                        st.session_state.tournament = {**t_found, "tee": tee_res.data[0]}
                        st.session_state.role = "leader"
                        st.session_state.screen = "leader_setup"
                        st.rerun()
                else:
                    st.info("No hay torneos activos.")

            with st.container(border=True):
                st.markdown('<div style="background:rgba(90,35,90,0.16);margin:-12px -12px 8px -12px;padding:6px 14px;border-radius:6px 6px 0 0;"><strong>Grupos del torneo</strong></div>', unsafe_allow_html=True)
                torneos_grupos = get_active_tournaments()
                if torneos_grupos:
                    grupos_t_opts = {t["name"]: t for t in torneos_grupos}
                    grupos_t_sel = st.selectbox("Torneo", list(grupos_t_opts.keys()), key="grupos_t_sel")
                    t_sel = grupos_t_opts[grupos_t_sel]
                    grupos_lista = supabase.table("groups").select("*").eq("tournament_id", t_sel["id"]).execute().data
                    if not grupos_lista:
                        st.info("No hay grupos creados para este torneo.")
                    else:
                        for g in grupos_lista:
                            players_g = supabase.table("group_players").select("*").eq("group_id", g["id"]).order("pair_order").execute().data
                            participantes = ", ".join([p["player_name"] for p in players_g]) if players_g else "Sin jugadores"
                            with st.expander(f"{g['name']} — Codigo: {g['access_code']}"):
                                st.caption(f"Participantes: {participantes}")
                                nuevo_nombre = st.text_input("Nombre del grupo", value=g["name"], key=f"edit_name_{g['id']}")
                                col_save, col_del = st.columns(2)
                                with col_save:
                                    if st.button("Guardar nombre", key=f"save_{g['id']}"):
                                        supabase.table("groups").update({"name": nuevo_nombre}).eq("id", g["id"]).execute()
                                        st.success("Nombre actualizado.")
                                        st.rerun()
                                with col_del:
                                    if st.button("Borrar grupo", key=f"del_g_{g['id']}", type="secondary"):
                                        supabase.table("guests").delete().eq("tournament_id", t_sel["id"]).in_("id",
                                            [r["guest_id"] for r in supabase.table("group_players").select("guest_id").eq("group_id", g["id"]).execute().data if r.get("guest_id")]
                                            or ["00000000-0000-0000-0000-000000000000"]
                                        ).execute()
                                        supabase.table("tournament_scores").delete().eq("group_id", g["id"]).execute()
                                        supabase.table("group_players").delete().eq("group_id", g["id"]).execute()
                                        supabase.table("groups").delete().eq("id", g["id"]).execute()
                                        st.success(f"Grupo {g['name']} borrado.")
                                        st.rerun()
                else:
                    st.info("No hay torneos activos.")

            with st.container(border=True):
                st.markdown('<div style="background:rgba(130,65,10,0.16);margin:-12px -12px 8px -12px;padding:6px 14px;border-radius:6px 6px 0 0;"><strong>Borrar torneo</strong></div>', unsafe_allow_html=True)
                torneos_admin = get_active_tournaments()
                if torneos_admin:
                    del_opts = {f"{t['name']} ({t['access_code']})": t for t in torneos_admin}
                    del_sel = st.selectbox("Torneo a borrar", list(del_opts.keys()), key="del_sel")
                    if st.button("Borrar torneo", type="secondary"):
                        t_del = del_opts[del_sel]
                        # 1. Borrar guests del torneo (FK a tournaments)
                        supabase.table("guests").delete().eq("tournament_id", t_del["id"]).execute()
                        # 2. Borrar scores por tournament_id (directo, cubre todos los casos)
                        supabase.table("tournament_scores").delete().eq("tournament_id", t_del["id"]).execute()
                        # 3. Borrar jugadores de cada grupo
                        grupos = supabase.table("groups").select("id").eq("tournament_id", t_del["id"]).execute().data
                        for g in grupos:
                            supabase.table("group_players").delete().eq("group_id", g["id"]).execute()
                        # 4. Borrar grupos
                        supabase.table("groups").delete().eq("tournament_id", t_del["id"]).execute()
                        # 5. Borrar torneo
                        supabase.table("tournaments").delete().eq("id", t_del["id"]).execute()
                        st.success(f"Torneo {t_del['name']} borrado.")
                        st.rerun()
                else:
                    st.info("No hay torneos para borrar.")

    with col_spec:
        st.subheader("Espectador")
        with st.container(border=True):
            st.markdown('<div style="background:rgba(10,95,95,0.16);margin:-12px -12px 8px -12px;padding:6px 14px;border-radius:6px 6px 0 0;"><strong>Ver Leaderboard</strong></div>', unsafe_allow_html=True)
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

        st.subheader("Lider de Grupo")
        with st.container(border=True):
            st.markdown('<div style="background:rgba(65,95,20,0.16);margin:-12px -12px 8px -12px;padding:6px 14px;border-radius:6px 6px 0 0;"><strong>Ingresar al grupo</strong></div>', unsafe_allow_html=True)
            st.caption("Ya tienes un codigo de grupo?")
            group_code = st.text_input("Codigo de grupo", key="group_code_input", placeholder="")
            if st.button("Ingresar al grupo", type="primary"):
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

# ==============================================================================
# PANTALLA LEADER SETUP
elif _screen == "leader_setup":
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
            group_code = gen_group_code()
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
                        guest_db_id = save_guest(j["name"], j["current_handicap"], t["date"], t["id"])
                    elif j.get("_hi_temporal"):
                        guest_db_id = save_guest(j["name"], j["current_handicap"], t["date"], t["id"], player_id=j["id"])
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
            st.success(f"Grupo creado! Codigo: {group_code}")
            st.session_state.screen = "home"
            st.rerun()
            time.sleep(2)
            st.rerun()

    st.stop()

# ==============================================================================
# PANTALLA SCORES
elif _screen == "scores":
    t = st.session_state.tournament
    g = st.session_state.group
    tee = t["tee"]
    parejas = st.session_state.parejas
    strokes_map = st.session_state.strokes_map
    holes = get_holes()

    # Persistir en URL para sobrevivir recargas/cierre de celular
    st.query_params["g"] = g["access_code"]
    st.query_params["h"] = str(st.session_state.hole_num)

    titulo = f"{t['name']} - {g['name']}"
    subtitulo = f"Tee: {tee['color']} | Codigo: {g['access_code']}"
    st.markdown(
        f'<p style="font-size:1.2em;font-weight:700;margin:0 0 2px 0;">{titulo}</p>'
        f'<p style="font-size:0.8em;color:#888;margin:0 0 8px 0;">{subtitulo}</p>',
        unsafe_allow_html=True
    )
    st.markdown("---")

    existing_scores = get_group_scores(t["id"], g["id"])
    existing_map = {}
    for s in existing_scores:
        existing_map[(s["pair_name"], s["hole_number"], s["player_id"], s["guest_id"])] = s["strokes"]
    hoyos_con_scores = set(s["hole_number"] for s in existing_scores)

    # Selector de hoyo
    hole_options = {}
    for h in range(1, 19):
        tiene = h in hoyos_con_scores
        hole_options[f"{"\u2705" if tiene else "\u2b1c"} Hoyo {h}"] = h

    current_label = next(k for k, v in hole_options.items() if v == st.session_state.hole_num)
    sel_label = st.selectbox("Selecciona hoyo", list(hole_options.keys()),
        index=list(hole_options.keys()).index(current_label), key="hole_selector")
    hole_num = hole_options[sel_label]
    st.session_state.hole_num = hole_num
    st.query_params["h"] = str(hole_num)
    hole_info = next(h for h in holes if h["hole_number"] == hole_num)
    st.markdown(f"**Hoyo {hole_num} - Par {hole_info['par']} | HCP: {hole_info['handicap']}**")

    # Colores por pareja (ciclico)
    PAIR_COLORS = [
        ("#1a472a", "#e8f5e9"),  # verde
        ("#1a3a5c", "#e3f0fb"),  # azul
        ("#6b2d2d", "#fdecea"),  # rojo
        ("#4a3000", "#fff8e1"),  # cafe/amarillo
        ("#2d1a4a", "#f3e5f5"),  # morado
        ("#005050", "#e0f7f7"),  # teal
        ("#3a3a00", "#f9f9e0"),  # olivo
        ("#3a0030", "#fce4f6"),  # magenta
    ]

    cols = st.columns(max(len(parejas), 1))
    scores_to_save = []

    for i, (pair_name, jugadores) in enumerate(parejas.items()):
        j1 = jugadores[0]
        j2 = jugadores[1]
        border_color, bg_color = PAIR_COLORS[i % len(PAIR_COLORS)]
        with cols[i]:
            with st.container(border=True):
                st.markdown(
                    f'<div style="background:{bg_color};border-left:5px solid {border_color};'
                    f'border-radius:6px;padding:8px 12px;margin-bottom:12px;">'
                    f'<strong style="color:{border_color};font-size:1.08em;">{pair_name}</strong></div>',
                    unsafe_allow_html=True
                )
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

                ventaja1 = f"+{sg1} ventaja" if sg1 > 0 else "Sin ventaja"
                ventaja2 = f"+{sg2} ventaja" if sg2 > 0 else "Sin ventaja"
                saved_icon1 = " \u2705" if saved1 else ""
                saved_icon2 = " \u2705" if saved2 else ""

                g1_val = st.number_input(
                    f"Golpes {j1['player_name']} | {ventaja1}{saved_icon1}",
                    min_value=1, max_value=15, value=prev1, key=f"g1_{pair_name}_{hole_num}"
                )
                g2_val = st.number_input(
                    f"Golpes {j2['player_name']} | {ventaja2}{saved_icon2}",
                    min_value=1, max_value=15, value=prev2, key=f"g2_{pair_name}_{hole_num}"
                )

                net1 = g1_val - sg1
                net2 = g2_val - sg2
                bola_baja = min(net1, net2)
                if net1 == net2:
                    n1 = j1["player_name"].split()[0]
                    n2 = j2["player_name"].split()[0]
                    ganador_hoyo = f"{n1}/{n2}"
                elif net1 < net2:
                    ganador_hoyo = j1["player_name"]
                else:
                    ganador_hoyo = j2["player_name"]
                vs_par = bola_baja - hole_info["par"]
                vs_par_str = f"+{vs_par}" if vs_par > 0 else str(vs_par)
                vs_color = "#c0392b" if vs_par > 0 else ("#27ae60" if vs_par < 0 else "#555")
                st.markdown(
                    f"""<div style="display:flex;gap:16px;align-items:center;padding:6px 2px 2px 2px;">
                        <div style="text-align:center;">
                            <div style="font-size:0.68em;color:#666;line-height:1.2;">Bola baja</div>
                            <div style="font-size:1.1em;font-weight:700;">{bola_baja}</div>
                        </div>
                        <div style="text-align:center;">
                            <div style="font-size:0.68em;color:#666;line-height:1.2;">Vs par</div>
                            <div style="font-size:1.1em;font-weight:700;color:{vs_color};">{vs_par_str}</div>
                        </div>
                        <div style="text-align:center;flex:1;min-width:0;">
                            <div style="font-size:0.68em;color:#666;line-height:1.2;">Aporta</div>
                            <div style="font-size:0.85em;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{ganador_hoyo}</div>
                        </div>
                    </div>""",
                    unsafe_allow_html=True
                )

                scores_to_save.append((pair_name, pid1, gid1, g1_val, net1))
                scores_to_save.append((pair_name, pid2, gid2, g2_val, net2))
    if st.button(f"Guardar Hoyo {hole_num}", type="primary"):
        for pair_name, pid, gid, strokes, net in scores_to_save:
            upsert_score(t["id"], g["id"], pair_name, pid, gid, hole_num, strokes, net)
        if hole_num < 18:
            st.session_state.hole_num = hole_num + 1
        st.rerun()

    st.markdown("---")
    st.subheader("Scoreboard del grupo")
    scores_db = get_group_scores(t["id"], g["id"])
    hole_scores = defaultdict(list)
    for s in scores_db:
        hole_scores[(s["pair_name"], s["hole_number"])].append(s["net_strokes"])

    group_board = []
    for pair_name, jugadores in parejas.items():
        j1 = jugadores[0]
        j2 = jugadores[1]
        front_total, front_hoyos = 0, set()
        back_total, back_hoyos = 0, set()
        for (pn, hn), nets in hole_scores.items():
            if pn == pair_name and len(nets) >= 2:
                bola = min(nets)
                if hn <= 9:
                    front_total += bola
                    front_hoyos.add(hn)
                else:
                    back_total += bola
                    back_hoyos.add(hn)
        total_hoyos = front_hoyos | back_hoyos
        total = front_total + back_total
        par_front = sum(h["par"] for h in holes if h["hole_number"] in front_hoyos)
        par_back = sum(h["par"] for h in holes if h["hole_number"] in back_hoyos)
        par_total = par_front + par_back
        group_board.append({
            "Pareja": pair_name,
            "Jugadores": f"{j1['player_name']} / {j2['player_name']}",
            "Front (1-9)": fmt_score(front_total, par_front, front_hoyos),
            "Back (10-18)": fmt_score(back_total, par_back, back_hoyos),
            "Total": fmt_score(total, par_total, total_hoyos),
            "Hoyos": f"{len(total_hoyos)}/18",
            "_sort": (total - par_total) if total_hoyos else 9999,
        })

    group_board.sort(key=lambda x: x["_sort"])
    gb_pos = 1
    for i, r in enumerate(group_board):
        if i > 0 and r["_sort"] != 9999 and group_board[i-1]["_sort"] == r["_sort"]:
            r["Ranking"] = group_board[i-1]["Ranking"]
        elif r["_sort"] == 9999:
            r["Ranking"] = "-"
        else:
            r["Ranking"] = gb_pos
        if r["_sort"] != 9999:
            gb_pos += 1
    for r in group_board:
        del r["_sort"]
    st.dataframe(pd.DataFrame(group_board)[["Ranking", "Pareja", "Jugadores", "Front (1-9)", "Back (10-18)", "Total", "Hoyos"]], use_container_width=True, hide_index=True)

    st.markdown("---")
    st.subheader("Detalle hoyo por hoyo")
    # Mapa: (pair_name, hole_number, player_id, guest_id) -> strokes brutos
    scores_map_detail = {}
    for s in scores_db:
        key = (s["pair_name"], s["hole_number"], s.get("player_id"), s.get("guest_id"))
        scores_map_detail[key] = s["strokes"]

    # Lista ordenada de todos los jugadores (nombre, pid, gid)
    jugadores_list = []
    for pair_name, jugs in parejas.items():
        for j in jugs:
            jugadores_list.append({
                "nombre": j["player_name"],
                "pair":   pair_name,
                "pid":    j.get("player_id"),
                "gid":    j.get("guest_id"),
            })

    if scores_map_detail:
        holes_par = {h["hole_number"]: h["par"] for h in holes}
        tabla_rows = []
        for hn in range(1, 19):
            par = holes_par.get(hn, 4)
            row = {"Hoyo": f"H{hn} (Par {par})"}
            tiene_algo = False
            for j in jugadores_list:
                val = scores_map_detail.get((j["pair"], hn, j["pid"], j["gid"]))
                if val is not None:
                    diff = val - par
                    diff_str = f"+{diff}" if diff > 0 else str(diff)
                    row[j["nombre"]] = f"{val} ({diff_str})"
                    tiene_algo = True
                else:
                    row[j["nombre"]] = "-"
            if tiene_algo:
                tabla_rows.append(row)

        if tabla_rows:
            cols_order = ["Hoyo"] + [j["nombre"] for j in jugadores_list]
            df_detalle = pd.DataFrame(tabla_rows)[cols_order]
            st.dataframe(df_detalle, use_container_width=True, hide_index=True)
        else:
            st.caption("Aún no hay scores capturados.")
    else:
        st.caption("Aún no hay scores capturados.")

    st.markdown("---")
    if st.button("Ver Leaderboard", use_container_width=True):
        st.session_state.screen = "leaderboard"
        st.query_params["g"] = g["access_code"]
        st.rerun()

    if st.button("Salir", use_container_width=True):
        go_home()
        st.rerun()

    st.stop()

# ==============================================================================
# PANTALLA LEADERBOARD
elif _screen == "leaderboard":
    t = st.session_state.tournament
    tee = t["tee"]
    holes = get_holes()

    EMOJI_GOLD = "\U0001F947"
    EMOJI_GOLF = "\U0001F3CC\uFE0F"
    lb_titulo = f"Leaderboard - {t['name']}"
    lb_sub    = f"Tee: {tee['color']} | Rating: {tee['rating']} | Slope: {tee['slope']}"
    st.markdown(
        f'<p style="font-size:1.2em;font-weight:700;margin:0 0 2px 0;">{lb_titulo}</p>'
        f'<p style="font-size:0.8em;color:#888;margin:0 0 8px 0;">{lb_sub}</p>',
        unsafe_allow_html=True
    )
    # Si venimos de un grupo, ofrecer volver a el
    _lb_group_code = st.query_params.get("g")
    if _lb_group_code:
        if st.button("Volver al grupo", use_container_width=True):
            st.session_state.screen = "scores"
            st.rerun()
    if st.button("Actualizar", key="refresh_leaderboard", use_container_width=True):
        st.rerun()

    st.markdown("---")

    groups = get_groups_for_tournament(t["id"])
    all_scores = get_all_scores(t["id"])

    hole_scores = defaultdict(list)
    for s in all_scores:
        hole_scores[(s["pair_name"], s["group_id"], s["hole_number"])].append(s["net_strokes"])

    # Calcular hoyos jugados por cada pareja
    pair_data = {}  # (pair_name, group_id) -> {hoyo: bola_baja}
    for grp in groups:
        rows = get_group_players(grp["id"])
        parejas_grp = agrupar_parejas(rows)
        for pair_name in parejas_grp:
            key = (pair_name, grp["id"])
            pair_data[key] = {}
            for (pn, gid, hn), nets in hole_scores.items():
                if pn == pair_name and gid == grp["id"] and len(nets) >= 2:
                    pair_data[key][hn] = min(nets)

    # Hoyos que TODAS las parejas han jugado (hoyos comunes)
    if pair_data:
        hoyos_por_pareja = [set(hoyos.keys()) for hoyos in pair_data.values()]
        hoyos_comunes = hoyos_por_pareja[0].intersection(*hoyos_por_pareja[1:]) if len(hoyos_por_pareja) > 1 else hoyos_por_pareja[0]
    else:
        hoyos_comunes = set()

    leader_data = []
    for grp in groups:
        rows = get_group_players(grp["id"])
        parejas_grp = agrupar_parejas(rows)
        for pair_name, jugadores in parejas_grp.items():
            j1 = jugadores[0]
            j2 = jugadores[1]
            key = (pair_name, grp["id"])
            bolas = pair_data.get(key, {})

            # Score en hoyos comunes (para ranking justo)
            front_common = {hn: bolas[hn] for hn in hoyos_comunes if hn in bolas and hn <= 9}
            back_common  = {hn: bolas[hn] for hn in hoyos_comunes if hn in bolas and hn > 9}
            total_common = sum(front_common.values()) + sum(back_common.values())
            par_common   = sum(h["par"] for h in holes if h["hole_number"] in hoyos_comunes)
            sort_val     = (total_common - par_common) if hoyos_comunes else 9999

            # Score real completo (para display)
            front_hoyos = {hn for hn in bolas if hn <= 9}
            back_hoyos  = {hn for hn in bolas if hn > 9}
            total_hoyos = front_hoyos | back_hoyos
            front_total = sum(bolas[hn] for hn in front_hoyos)
            back_total  = sum(bolas[hn] for hn in back_hoyos)
            total       = front_total + back_total
            par_front   = sum(h["par"] for h in holes if h["hole_number"] in front_hoyos)
            par_back    = sum(h["par"] for h in holes if h["hole_number"] in back_hoyos)
            par_total   = par_front + par_back

            # Front/Back comunes para lideres de vuelta
            front_common_set = {hn for hn in hoyos_comunes if hn <= 9}
            back_common_set  = {hn for hn in hoyos_comunes if hn > 9}
            front_common_score = sum(bolas.get(hn, 0) for hn in front_common_set)
            back_common_score  = sum(bolas.get(hn, 0) for hn in back_common_set)
            par_front_common   = sum(h["par"] for h in holes if h["hole_number"] in front_common_set)
            par_back_common    = sum(h["par"] for h in holes if h["hole_number"] in back_common_set)

            leader_data.append({
                "Ranking": 0,
                "Grupo": grp["name"],
                "Pareja": pair_name,
                "Jugadores": f"{j1['player_name']} / {j2['player_name']}",
                "Front (1-9)": fmt_score(front_total, par_front, front_hoyos),
                "Back (10-18)": fmt_score(back_total, par_back, back_hoyos),
                "Total": fmt_score(total, par_total, total_hoyos),
                "Hoyos": f"{len(total_hoyos)}/18",
                "Hoyos Ranking": f"{len(hoyos_comunes)} comunes",
                "_sort":  sort_val,
                "_front": (front_common_score - par_front_common) if front_common_set else 9999,
                "_back":  (back_common_score  - par_back_common)  if back_common_set  else 9999,
            })

    leader_data.sort(key=lambda x: x["_sort"])
    # Asignar posiciones con empate
    pos = 1
    for i, r in enumerate(leader_data):
        if i > 0 and r["_sort"] != 9999 and leader_data[i-1]["_sort"] == r["_sort"]:
            r["Ranking"] = leader_data[i-1]["Ranking"]
        elif r["_sort"] == 9999:
            r["Ranking"] = "-"
        else:
            r["Ranking"] = pos
        if r["_sort"] != 9999:
            pos += 1
    con_datos = [r for r in leader_data if r["_sort"] != 9999]
    con_front = [r for r in leader_data if r["_front"] != 9999]
    con_back  = [r for r in leader_data if r["_back"] != 9999]

    if con_datos:
        best_total = min(r["_sort"] for r in con_datos)
        lideres_total = [r for r in con_datos if r["_sort"] == best_total]
        names_total = " | ".join([f"**{r['Pareja']}** ({r['Jugadores']}) [{r['Grupo']}]" for r in lideres_total])
        score_total = lideres_total[0]["Total"]
        prefix_total = "EMPATE " if len(lideres_total) > 1 else ""
        st.success(f"{EMOJI_GOLD} {prefix_total}Total: {names_total} - {score_total}")
    if con_front:
        best_front = min(r["_front"] for r in con_front)
        lideres_front = [r for r in con_front if r["_front"] == best_front]
        names_front = " | ".join([f"**{r['Pareja']}** ({r['Jugadores']}) [{r['Grupo']}]" for r in lideres_front])
        score_front = lideres_front[0]["Front (1-9)"]
        prefix_front = "EMPATE " if len(lideres_front) > 1 else ""
        st.info(f"{EMOJI_GOLF} {prefix_front}Front: {names_front} - {score_front}")
    if con_back:
        best_back = min(r["_back"] for r in con_back)
        lideres_back = [r for r in con_back if r["_back"] == best_back]
        names_back = " | ".join([f"**{r['Pareja']}** ({r['Jugadores']}) [{r['Grupo']}]" for r in lideres_back])
        score_back = lideres_back[0]["Back (10-18)"]
        prefix_back = "EMPATE " if len(lideres_back) > 1 else ""
        st.info(f"{EMOJI_GOLF} {prefix_back}Back: {names_back} - {score_back}")

    for r in leader_data:
        del r["_sort"]
        del r["_front"]
        del r["_back"]

    if not leader_data:
        st.info("No hay grupos con informacion aun.")
    else:
        st.dataframe(
            pd.DataFrame(leader_data)[["Ranking", "Grupo", "Pareja", "Jugadores", "Front (1-9)", "Back (10-18)", "Total", "Hoyos", "Hoyos Ranking"]],
            use_container_width=True, hide_index=True
        )


    st.markdown("---")
    st.subheader("Detalle hoyo por hoyo")

    # Construir lista de jugadores en orden: grupo > pareja > jugador
    lb_jugadores = []
    seen_players = set()
    for grp in groups:
        rows = get_group_players(grp["id"])
        parejas_grp = agrupar_parejas(rows)
        for pair_name, jugs in parejas_grp.items():
            for j in jugs:
                pid = j.get("player_id")
                gid = j.get("guest_id")
                key = (grp["id"], pair_name, pid, gid)
                if key not in seen_players:
                    seen_players.add(key)
                    lb_jugadores.append({
                        "nombre":   j["player_name"],
                        "pair":     pair_name,
                        "group_id": grp["id"],
                        "pid":      pid,
                        "gid":      gid,
                    })

    # Mapa de scores brutos: (group_id, pair_name, hole_number, pid, gid) -> strokes
    lb_scores_map = {}
    for s in all_scores:
        k = (s["group_id"], s["pair_name"], s["hole_number"], s.get("player_id"), s.get("guest_id"))
        lb_scores_map[k] = s["strokes"]

    if lb_scores_map:
        holes_par_lb = {h["hole_number"]: h["par"] for h in holes}
        lb_rows = []
        for hn in range(1, 19):
            par = holes_par_lb.get(hn, 4)
            row = {"Hoyo": f"H{hn} (Par {par})"}
            tiene_algo = False
            for j in lb_jugadores:
                val = lb_scores_map.get((j["group_id"], j["pair"], hn, j["pid"], j["gid"]))
                if val is not None:
                    diff = val - par
                    diff_str = f"+{diff}" if diff > 0 else str(diff)
                    row[j["nombre"]] = f"{val} ({diff_str})"
                    tiene_algo = True
                else:
                    row[j["nombre"]] = "-"
            if tiene_algo:
                lb_rows.append(row)

        if lb_rows:
            cols_lb = ["Hoyo"] + [j["nombre"] for j in lb_jugadores]
            st.dataframe(pd.DataFrame(lb_rows)[cols_lb], use_container_width=True, hide_index=True)
        else:
            st.caption("Aún no hay scores capturados.")
    else:
        st.caption("Aún no hay scores capturados.")

    if st.button("Salir", use_container_width=True):
        go_home()
        st.rerun()

    st.stop()















