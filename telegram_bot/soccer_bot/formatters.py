"""
Render data rows as Telegram HTML messages. Tables go inside <pre> so columns align.
"""
from html import escape

from soccer_bot.leagues import LEAGUES, league_label

RESULT_ICON = {"W": "✅", "D": "➖", "L": "❌"}
STREAK_LABEL = {"W": ("victoria", "victorias"), "D": ("empate", "empates"), "L": ("derrota", "derrotas")}
VENUE_LABEL = {"home": "LOCAL", "away": "VISITANTE", None: "TOTAL"}

TEST_BANNER = "🧪 <b>DATOS DE PRUEBA</b> — no usar para apostar\n\n"


def _num(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value) -> str:
    n = _num(value)
    return "-" if n is None else str(int(n))


def _pct(part, whole) -> str:
    p, w = _num(part), _num(whole)
    if p is None or not w:
        return "-"
    return f"{round(100 * p / w)}%"


def _ratio(value) -> str:
    n = _num(value)
    return "-" if n is None else f"{round(100 * n)}%"


def _signed(value) -> str:
    return f"{value:+d}"


def form_icons(form: str | None, n: int = 10) -> str:
    if not form:
        return "sin datos"
    return "".join(RESULT_ICON.get(c, "·") for c in form[-n:])


def _venue_metrics(stats: dict, venue: str) -> dict:
    s = f"_{venue}"
    played = _num(stats.get(f"fixtures_played{s}")) or 0
    gf = _num(stats.get(f"goals_for_total{s}")) or 0
    ga = _num(stats.get(f"goals_against_total{s}")) or 0
    return {
        "played": int(played),
        "wins": stats.get(f"fixtures_wins{s}"),
        "draws": stats.get(f"fixtures_draws{s}"),
        "losses": stats.get(f"fixtures_losses{s}"),
        "gf": int(gf),
        "ga": int(ga),
        "gf_avg": f"{gf / played:.2f}" if played else "-",
        "ga_avg": f"{ga / played:.2f}" if played else "-",
        "clean": stats.get(f"clean_sheets{s}"),
        "fts": stats.get(f"failed_to_score{s}"),
    }


def _header(name: str, league_id: int, season) -> str:
    return f"⚽ <b>{escape(name)}</b>\n{league_label(league_id)} · Temporada {season}\n"


def _recent_form_block(stats: dict) -> str:
    lines = [f"\n<b>Últimos 10</b> (todas las sedes, más reciente →)", form_icons(stats.get("form"), 10)]
    l10 = stats.get("last_10_total")
    if l10:
        lines.append(
            f"V {_int(stats.get('last_10_wins'))} · E {_int(stats.get('last_10_draws'))} · "
            f"D {_int(stats.get('last_10_losses'))}  ({_ratio(stats.get('last_10_win_ratio'))} victorias)"
        )
    streak_type, streak = stats.get("last_10_streak_type"), stats.get("last_10_streak_count")
    if streak_type in STREAK_LABEL and streak:
        singular, plural = STREAK_LABEL[streak_type]
        lines.append(f"Racha actual: {_int(streak)} {singular if int(streak) == 1 else plural}")
    return "\n".join(lines)


def format_form(stats: dict, venue: str | None) -> str:
    out = [_header(stats["name"], stats["league"], stats["season"])]

    if venue:
        m = _venue_metrics(stats, venue)
        diff = m["gf"] - m["ga"]
        out.append(f"<b>Como {VENUE_LABEL[venue]}</b> (temporada completa)")
        out.append(
            "<pre>"
            f"Partidos         {m['played']}\n"
            f"Victorias        {_int(m['wins']):>3}  ({_pct(m['wins'], m['played'])})\n"
            f"Empates          {_int(m['draws']):>3}  ({_pct(m['draws'], m['played'])})\n"
            f"Derrotas         {_int(m['losses']):>3}  ({_pct(m['losses'], m['played'])})\n"
            f"Goles a favor    {m['gf']:>3}  ({m['gf_avg']}/p)\n"
            f"Goles en contra  {m['ga']:>3}  ({m['ga_avg']}/p)\n"
            f"Diferencia       {_signed(diff):>3}\n"
            f"Valla invicta    {_int(m['clean']):>3}  ({_pct(m['clean'], m['played'])})\n"
            f"Sin marcar       {_int(m['fts']):>3}  ({_pct(m['fts'], m['played'])})\n"
            f"Mayor victoria   {escape(str(stats.get(f'biggest_wins_{venue}') or '-')):>5}\n"
            f"Mayor derrota    {escape(str(stats.get(f'biggest_losses_{venue}') or '-')):>5}"
            "</pre>"
        )
    else:
        h, a, t = (_venue_metrics(stats, v) for v in ("home", "away", "total"))
        rows = [
            ("Partidos", "played"), ("Victorias", "wins"), ("Empates", "draws"),
            ("Derrotas", "losses"), ("Goles favor", "gf"), ("Goles contra", "ga"),
            ("GF/partido", "gf_avg"), ("GC/partido", "ga_avg"),
            ("Valla invicta", "clean"), ("Sin marcar", "fts"),
        ]
        table = [f"{'':<14}{'Local':>6}{'Visit':>6}{'Total':>6}"]
        for label, key in rows:
            vals = [str(x[key]) if key.endswith("avg") else _int(x[key]) for x in (h, a, t)]
            table.append(f"{label:<14}{vals[0]:>6}{vals[1]:>6}{vals[2]:>6}")
        out.append("<b>Temporada completa</b>")
        out.append("<pre>" + "\n".join(table) + "</pre>")

    out.append(_recent_form_block(stats))
    return "\n".join(out)


def format_compare(a: dict, b: dict, venue: str | None) -> str:
    v = venue or "total"
    ma, mb = _venue_metrics(a, v), _venue_metrics(b, v)
    na, nb = a["name"][:9], b["name"][:9]
    rows = [
        ("Partidos", ma["played"], mb["played"]),
        ("Victorias", _int(ma["wins"]), _int(mb["wins"])),
        ("% Victorias", _pct(ma["wins"], ma["played"]), _pct(mb["wins"], mb["played"])),
        ("Empates", _int(ma["draws"]), _int(mb["draws"])),
        ("Derrotas", _int(ma["losses"]), _int(mb["losses"])),
        ("GF/partido", ma["gf_avg"], mb["gf_avg"]),
        ("GC/partido", ma["ga_avg"], mb["ga_avg"]),
        ("Diferencia", _signed(ma["gf"] - ma["ga"]), _signed(mb["gf"] - mb["ga"])),
        ("Valla invicta", _int(ma["clean"]), _int(mb["clean"])),
        ("% U10 ganados", _ratio(a.get("last_10_win_ratio")), _ratio(b.get("last_10_win_ratio"))),
    ]
    table = [f"{'':<14}{escape(na):>10}{escape(nb):>10}"]
    table += [f"{label:<14}{str(x):>10}{str(y):>10}" for label, x, y in rows]

    return "\n".join([
        f"⚔️ <b>{escape(a['name'])}</b> vs <b>{escape(b['name'])}</b>",
        f"Como {VENUE_LABEL[venue]} · temporada completa" if venue else "Temporada completa · local + visitante",
        "<pre>" + "\n".join(table) + "</pre>",
        f"\n<b>Últimos 10</b> (más reciente →)",
        f"{escape(na)}: {form_icons(a.get('form'))}",
        f"{escape(nb)}: {form_icons(b.get('form'))}",
    ])


def format_ranking(rows: list[dict], league_id: int) -> str:
    if not rows:
        return f"No hay ranking para {league_label(league_id)}."
    table = [f"{'#':>2} {'Equipo':<16}{'Score':>6} {'U5':>4}"]
    for r in rows:
        table.append(
            f"{_int(r['rank_by_win_score']):>2} {escape(r['team_name'][:16]):<16}"
            f"{_num(r['win_score']) or 0:>6.1f} {_ratio(r.get('last_5_win_ratio')):>4}"
        )
    return "\n".join([
        f"🏆 <b>Ranking de forma</b> · {league_label(league_id)} · {rows[0]['season']}",
        "<pre>" + "\n".join(table) + "</pre>",
        "<i>Score = ponderado de % victorias en los últimos 10, 5 y 3. U5 = % victorias últimos 5.</i>",
    ])


def _cards_block(row: dict | None, color: str) -> str:
    icon, label = ("🟨", "Amarillas") if color == "yellow" else ("🟥", "Rojas")
    if not row:
        return f"{icon} <b>{label}</b>: sin datos"
    return (
        f"{icon} <b>{label}</b> · riesgo {escape(str(row.get('risk_category', '-')))}\n"
        "<pre>"
        f"Prob. próximo partido  {_num(row.get(f'{color}_card_probability_pct')) or 0:.1f}%\n"
        f"Esperadas próx. part.  {_num(row.get(f'expected_{color}_cards_next_match')) or 0:.2f}\n"
        f"Promedio por partido   {_num(row.get(f'avg_{color}_cards_per_game')) or 0:.2f}\n"
        f"Total temporada        {_int(row.get(f'total_{color}_cards'))}\n"
        f"Periodo pico (min)     {escape(str(row.get(f'peak_{color}_card_period') or '-'))}"
        "</pre>"
    )


def format_cards(team: dict, cards: dict) -> str:
    any_row = cards.get("yellow") or cards.get("red") or {}
    season = any_row.get("season", team.get("season"))
    return "\n".join([
        _header(team["name"], team["league"], season),
        _cards_block(cards.get("yellow"), "yellow"),
        _cards_block(cards.get("red"), "red"),
    ])


def format_team_list(teams: list[dict], league_id: int) -> str:
    if not teams:
        return f"No hay equipos cargados para {league_label(league_id)}."
    names = sorted(escape(t["name"]) for t in teams)
    return f"📋 <b>Equipos</b> · {league_label(league_id)} ({len(names)})\n\n" + "\n".join(names)


def format_ambiguous(query: str, matches: list[dict]) -> str:
    options = "\n".join(f"• {escape(m['name'])} ({league_label(m['league'])})" for m in matches[:10])
    return f"🤔 «{escape(query)}» coincide con varios equipos:\n{options}\n\nEscribe el nombre más completo."


def format_leagues() -> str:
    lines = [f"{lg.flag} <b>{lg.name}</b> — <code>{lg.aliases[0]}</code>" for lg in LEAGUES]
    return "🌍 <b>Ligas disponibles</b>\n\n" + "\n".join(lines)


def format_quota(remaining: int, max_questions: int, resets_at, tz) -> str:
    text = f"🎟️ Te quedan <b>{remaining} de {max_questions}</b> preguntas libres."
    if resets_at and remaining < max_questions:
        text += f" Se libera una el {resets_at.astimezone(tz):%d/%m a las %H:%M}."
    return text


def format_usage(stats: dict) -> str:
    return (
        f"📊 <b>Uso de Claude</b> · últimos {stats['days']} días\n<pre>"
        f"Preguntas      {stats['questions']}\n"
        f"Usuarios       {stats['users']}\n"
        f"Tokens entrada {stats['input_tokens']:,}\n"
        f"Tokens salida  {stats['output_tokens']:,}\n"
        f"Costo estimado ${stats['cost_usd']:.2f} USD"
        "</pre>"
    )


def format_suggestions(data: dict) -> str:
    out = ["🧠 <b>Backlog de comandos</b> (de las preguntas en lenguaje natural)"]
    if data["categories"]:
        out.append("\n<b>Lo que piden y aún no tenemos</b>")
        out += [f"• {escape(r['category'])}: {r['n']}" for r in data["categories"]]
    if data["tools"]:
        out.append("\n<b>Comandos más pedidos en lenguaje natural</b>")
        out += [f"• /{escape(r['tool'])}: {r['n']}" for r in data["tools"]]
    if data["recent"]:
        out.append("\n<b>Ejemplos recientes</b>")
        out += [f"• [{escape(r['category'])}] {escape(r['summary'] or r['question'][:80])}" for r in data["recent"]]
    if len(out) == 1:
        out.append("\nAún no hay preguntas registradas.")
    return "\n".join(out)


HELP = """🤖 <b>Soccer Stats Bot</b>

<b>/forma</b> &lt;equipo&gt; [local|visitante]
  Victorias, goles y forma reciente
  <code>/forma Barcelona visitante</code>

<b>/comparar</b> &lt;equipo&gt; vs &lt;equipo&gt; [local|visitante]
  <code>/comparar Arsenal vs Chelsea</code>

<b>/ranking</b> &lt;liga&gt;
  <code>/ranking premier</code>

<b>/tarjetas</b> &lt;equipo&gt;
  Probabilidad de amarillas y rojas
  <code>/tarjetas Sevilla</code>

<b>/equipos</b> &lt;liga&gt; · <b>/ligas</b> · <b>/cupo</b> · <b>/ayuda</b>

💬 También puedes escribir tu pregunta normal, p. ej. "¿cómo le va al Barça de visitante?". Las preguntas libres son limitadas (ver /cupo); los comandos son ilimitados.

ℹ️ Las estadísticas son por temporada. "Últimos N partidos" con goles y local/visitante llegará con la tabla de partidos (fixtures)."""
