"""Reviewed mappings used by the AOC regional enrichment stage."""

from __future__ import annotations


REGION_OVERRIDES_BY_ID = {
    # Saussignac: hand-reviewed Dordogne assignment after broad polygon overlap review.
    "74": "Dordogne",
    # Cremant du Jura: Jura AOC corrected from broad regional polygon assignment.
    "127": "Jura",
    # Cotes du Jura: Jura AOC corrected from broad regional polygon assignment.
    "155": "Jura",
    # Macvin du Jura: Jura AOC corrected from broad regional polygon assignment.
    "173": "Jura",
    # Arbois: Jura AOC corrected from broad regional polygon assignment.
    "176": "Jura",
    # Chateau-Chalon: Jura AOC corrected from broad regional polygon assignment.
    "187": "Jura",
    # L'Etoile: Jura AOC corrected from broad regional polygon assignment.
    "244": "Jura",
}

REGION_OVERRIDE_METADATA = {
    "74": {"appellation": "Saussignac", "reason": "Reviewed Dordogne override from notebook"},
    "127": {"appellation": "Crémant du Jura", "reason": "Reviewed Jura override from notebook"},
    "155": {"appellation": "Côtes du Jura", "reason": "Reviewed Jura override from notebook"},
    "173": {"appellation": "Macvin du Jura", "reason": "Reviewed Jura override from notebook"},
    "176": {"appellation": "Arbois", "reason": "Reviewed Jura override from notebook"},
    "187": {"appellation": "Château-Chalon", "reason": "Reviewed Jura override from notebook"},
    "244": {"appellation": "L'Etoile", "reason": "Reviewed Jura override from notebook"},
}

FALLBACK_REGIONS_BY_DT = {
    "Dijon": "Bourgogne",
    "Gaillac": "Sud-Ouest",
    "Pau": "Sud-Ouest",
    "Tours": "Loire",
    "Valence": "Rhône",
    "Corse": "Corse",
    "Colmar": "Alsace",
    "Angers": "Loire",
    "Bordeaux": "Bordeaux",
    "Montpellier": "Languedoc-Roussillon",
    "Avignon": "Rhône",
    "Narbonne": "Languedoc-Roussillon",
    "La Valette du Var": "Provence",
    # Intentional notebook-reviewed delegation fallback.
    "Mâcon": "Savoie",
}

WINE_REGION_COLORS = {
    "Rhône": "#7B1E3C",
    "Provence": "#B983A2",
    "Loire": "#556B2F",
    "Bordeaux": "#264653",
    "Languedoc-Roussillon": "#6A4E77",
    "Dordogne": "#C9B037",
    "Bourgogne": "#3B3C88",
    "Alsace": "#3A5F5F",
    "Corse": "#8B0000",
    "Sud-Ouest": "#D2691E",
    "Savoie": "#8FBDBD",
    "Jura": "#88A378",
}

