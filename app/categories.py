"""Topics shown on the jukebox selector. Each one is an SVS search term.

Search runs against Earth Information Center content first (made_for=EIC),
then fills with general SVS results. An empty query = latest EIC visualizations.
First batch, picked to match EIC themes (climate, oceans, ice, atmosphere, land).
Trim or reorder after testing.
"""

CATEGORIES: list[dict[str, str]] = [
    {"label": "EIC Latest", "query": ""},
    {"label": "Global Temp", "query": "global temperature"},
    {"label": "Sea Ice", "query": "sea ice"},
    {"label": "Greenland Ice", "query": "greenland ice sheet"},
    {"label": "Antarctica", "query": "antarctica"},
    {"label": "Glaciers", "query": "glacier"},
    {"label": "Sea Level Rise", "query": "sea level"},
    {"label": "Ocean Currents", "query": "ocean currents"},
    {"label": "Ocean Temp", "query": "sea surface temperature"},
    {"label": "Phytoplankton", "query": "phytoplankton"},
    {"label": "El Nino", "query": "el nino"},
    {"label": "Hurricanes", "query": "hurricane"},
    {"label": "Precipitation", "query": "precipitation"},
    {"label": "Clouds", "query": "clouds"},
    {"label": "Aerosols", "query": "aerosols"},
    {"label": "Carbon Dioxide", "query": "carbon dioxide"},
    {"label": "Methane", "query": "methane"},
    {"label": "Ozone", "query": "ozone"},
    {"label": "Air Quality", "query": "air quality"},
    {"label": "Wildfires", "query": "wildfire"},
    {"label": "Drought", "query": "drought"},
    {"label": "Vegetation", "query": "vegetation"},
    {"label": "Snow Cover", "query": "snow cover"},
    {"label": "Night Lights", "query": "night lights"},
    {"label": "Earth at Night", "query": "black marble"},
]
