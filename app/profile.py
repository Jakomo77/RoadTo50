"""
Das Creator-Profil: Ziel, Philosophie, Ton und Grenzen.
Wird in jeden KI-Aufruf (Caption-Generierung, Trend-Scan) als Kontext eingespeist,
damit die KI IMMER weiss, wofuer dieser Account steht.
"""

CREATOR_PROFILE = {
    "name": "Jakob",
    "projekt": "Road to 50",
    "ziel": (
        "Countdown zum 50. Geburtstag am 03.08.2027. Meisterung von 3 Skills bis dahin: "
        "Handstand, Human Flag, Profi-Seilspringen (Cross-Overs, Double-Unders)."
    ),
    "philosophie": (
        "Du kannst dein Leben nicht verlaengern, sondern nur aufhoeren es zu verkuerzen. "
        "Aelterwerden ist biologisch, Verfall ist eine Entscheidung. Fokus auf Beseitigung "
        "von Schadfaktoren und Aufbau robuster relativer Maximalkraft, nicht auf Esoterik "
        "oder teure Supplements."
    ),
    "zielgruppe": "Maenner 35-60, die das Midlife-Abstiegs-Narrativ ablehnen.",
    "ton": (
        "Direkt, diszipliniert, authentisch, nahbar, mit trockenem Humor ueber das Aeltwerden. "
        "Radikale Authentizitaet statt KI-Fiktion oder Hochglanz."
    ),
    "no_gos": [
        "Keine peinlichen Trend-Taenze ohne Substanz",
        "Kein Clickbait ohne echten Inhalt",
        "Keine Esoterik, keine unbelegten Supplement-Versprechen",
    ],
    "content_matrix": {
        "Education": 0.50,
        "Inspiration": 0.20,
        "Entertainment": 0.20,
        "Promotion": 0.10,
    },
    "skills": ["Handstand", "Human Flag", "Seilspringen"],
}


def profile_as_prompt_block() -> str:
    p = CREATOR_PROFILE
    return (
        f"PROJEKT: {p['projekt']}\n"
        f"ZIEL: {p['ziel']}\n"
        f"PHILOSOPHIE: {p['philosophie']}\n"
        f"ZIELGRUPPE: {p['zielgruppe']}\n"
        f"TON: {p['ton']}\n"
        f"NO-GOS: {', '.join(p['no_gos'])}\n"
        f"CONTENT-MATRIX (Ziel-Verteilung): "
        f"{', '.join(f'{k} {int(v*100)}%' for k, v in p['content_matrix'].items())}"
    )
