# Michelin Guide to France 2024

----

## Changes from 2023 Guide

| Change in the Guide | Restaurants |
|---------------------|-------------|
| Additions           |             |
| Deletions           |             |
| Promotions          |             |
| Demotions           |             |


### From [Reddit](https://www.reddit.com/r/finedining/comments/1b7bzwu/michelin_france_2024_list_of_31_demoted_omitted/):

```markdown
## Going from three to two stars:

René and Maxime Meilleur, Saint-Martin-de-Belleville (Savoie)

----

## Going from two to one star:

Auberge du Cheval Blanc, Lembach (Bas-Rhin)

----

## Moving from one star to a simple recommendation:

Nature *, Armentières (North)

Les Oliviers *, Bandol (Var)

Le Bénaton, Beaune (Côte-d’Or)

Val d’Auge, Bondues (North)

René' Sens *, La Cadière-d'Azur (Var)

La Signoria *, Calvi (Upper Corsica)

La Barbacane, Carcassonne (Aude)

Hostellerie de l'Abbaye de la Celle, La Celle (Var)

Château de Courban *, Courban (Côte-d’Or)

Le 1825 - La Table *, Gesté (Maine-et-Loire)

La Table de la Mainaz, Gex (Ain)

Le Chiquito, Méry-sur-Oise (Val-d'Oise)

Roza Jin *, Nantes (Loire-Atlantique)

ERH *, Paris 1st arrondissement

Auberge Nicolas Flamel *, Paris 2nd

Ogata *, Paris 3rd

L'Atelier de Joël Robuchon - St-Germain *, Paris 3rd

ASPIC, Paris 7th

La Condesa, Paris 9th

La Dune du Château de Sable *, Porspoder (Finistère)

Le Foch, Reims (Marne)

Le Sérac, Saint-Gervais-les-Bains (Haute-Savoie)

Au Déjeuner de Sousceyrac, Sousceyrac-en-Quercy (Lot)

Buerehiesel, Strasbourg (Bas-Rhin)

Le Cénacle, Toulouse (Haute-Garonne)

*: Among the 26 one-star restaurants demoted due to the quality of the cuisine, 12 were also demoted due to a change or departure of chef, a sale or prolonged closure.
```

#### Specifics

---

## [2024 Visualisation Notebook](Notebooks/France/France_Visualisations.ipynb)

----

## Data processing:

### 1. [`Data-Preparation.ipynb`](Notebooks/Data-Preparation.ipynb):

The Michelin Guide data was partitioned by country to isolate the data relevant to France. The processed dataset `france_master.csv` was exported.

### 2. [`France_Processing.ipynb`](Notebooks/France/France_Processing.ipynb): 

This notebook performs exploratory data analysis and further processing. It outlines the steps to merge the restaurant data with the socio-economic and geospatial JSON data.

### 3. [`France_Arrondissements.ipynb`](Notebooks/France/France_Arrondissements.ipynb): 

The data was further granulated to *arrondissement* level

----

#### Output exported:

- `all_restaurants.csv` - Restaurant data appended with regional information.
- `all_restaurants(arrondissements).csv` - Restaurant data appended with arrondissement information.
- `arrondissement_restaurants.geojson` - Restaurant data and arrondissement GeoJSON data
- `department_restaurants.geojson` - Restaurant data and departmental GeoJSON data.
- `region_restaurants.geojson` - Restaurant data and regional GeoJSON data.

