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
