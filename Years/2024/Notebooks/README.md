
## [Visualisation Notebook](France/France_Visualisations.ipynb)

----

## Data processing:

#### 1. [`Data-Preparation.ipynb`](Data-Preparation.ipynb):

The Michelin Guide data was partitioned by country to isolate the data relevant to France. The processed dataset `france_master.csv` was exported.

#### 2. [`France_Processing.ipynb`](France/France_Processing.ipynb): 

This notebook performs exploratory data analysis and further processing. It outlines the steps to merge the restaurant data with the socio-economic and geospatial JSON data.

#### 3. [`France_Arrondissements.ipynb`](France/France_Arrondissements.ipynb): 

The data was further granulated to *arrondissement* level

----

#### Output exported:

- `all_restaurants.csv` - Restaurant data appended with regional information.
- `all_restaurants(arrondissements).csv` - Restaurant data appended with arrondissement information.
- `arrondissement_restaurants.geojson` - Restaurant data and arrondissement GeoJSON data
- `department_restaurants.geojson` - Restaurant data and departmental GeoJSON data.
- `region_restaurants.geojson` - Restaurant data and regional GeoJSON data.

----

