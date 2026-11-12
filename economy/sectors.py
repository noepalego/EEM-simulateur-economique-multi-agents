"""Secteurs de l'économie simulée."""

SKILL_SECTORS = (
    "food", "goods", "technology", "real_estate", "materials", "bank", "energy", "services",
)

# Secteurs d'entreprises, puis la fonction publique (employeur sans entreprises)
FIRM_SECTORS = (
    ("food", "Alimentation"),
    ("goods", "Biens courants"),
    ("technology", "Technologie"),
    ("real_estate", "Immobilier"),
    ("materials", "Matières premières"),
    ("energy", "Énergie"),
    ("bank", "Banque"),
    ("services", "Services (commerce, transport, formation)"),
)
SECTORS = FIRM_SECTORS + (("public", "Fonction publique"),)

# Libellés d'affichage seulement. Les médias (lot M) sont des entreprises créées par leur propre
# module : ils ne sont pas dans ``SECTORS`` (dont l'ordre règle la création des entreprises et le
# tirage des événements), mais l'interface doit savoir les nommer.
SECTOR_LABELS = {**dict(SECTORS), "media": "Médias"}


def sector_label(sector: str) -> str:
    """Libellé d'un secteur pour l'affichage ; l'identifiant lui-même s'il est inconnu."""
    return SECTOR_LABELS.get(sector, sector)
