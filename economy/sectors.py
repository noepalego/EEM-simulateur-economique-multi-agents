"""Secteurs de l'économie simulée."""

from collections.abc import Iterable

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
SECTOR_LABELS = {**dict(SECTORS), "media": "Médias", "vice": "Tabac & alcool", "marketing": "Marketing"}


def sector_label(sector: str) -> str:
    """Libellé d'un secteur pour l'affichage ; l'identifiant lui-même s'il est inconnu."""
    return SECTOR_LABELS.get(sector, sector)


def display_sectors(
    present: Iterable[str] = (), *, include_public: bool = True,
) -> tuple[tuple[str, str], ...]:
    """Secteurs historiques puis secteurs supplémentaires présents, sans changer l'initialisation.

    Les médias, le tabac et les agences ont leur propre création : ils complètent les lectures,
    mais ne doivent pas être ajoutés à ``SECTORS``, qui pilote les tirages du moteur.
    """
    base = SECTORS if include_public else FIRM_SECTORS
    known = {sector for sector, _ in SECTORS}
    extra = sorted(set(present) - known)
    return base + tuple((sector, sector_label(sector)) for sector in extra)
