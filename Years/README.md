# Data Aggregation and Processing

Processing utilizes the unique feature of the French postal code, whereby the initial two digits denote a specific department. 

France's administrative divisions are structured hierarchically:

- Regions (Régions): The top-level division, with mainland France having 13 regions.
- Departments (Départements): Each region is subdivided into departments, with 96 in mainland France.
- Arrondissements: Departments are further divided into arrondissements for administrative purposes, with 320 in mainland France, excluding the 20 municipal arrondissements of Paris.

----

### [`France_Departments_Demographics.ipynb`](../ExtraData/France_Departments_Demographics.ipynb): 

This notebook is common to all years and was used to aggregate socio-economic data from different sources. Departmental and regional data were scraped from Wikipedia, whereas population statistics were fetched from INSEE. 
   - Output exported:
       - `departments.csv`
       - `demographics.csv`

----

## Data processing (unique to each year):

### 1. `Data-Preparation.ipynb`:

The Michelin Guide data was partitioned by country to isolate the data relevant to France. The processed dataset `france_data.csv` was subsequently exported.

### 2. `France_Arrondissements.ipynb`: 

The data was further granulated to *arrondissement* level

### 3. `France_Processing.ipynb`: 

This notebook performs exploratory data analysis and further processing. It outlines the steps to merge the restaurant data with the socio-economic and geospatial JSON data.

Output exported:

- `all_restaurants.csv` - Restaurant data appended with regional information.
- `all_restaurants(arrondissements).csv` - Restaurant data appended with arrondissement information.
- `arrondissement_restaurants.geojson` - Restaurant data and arrondissement GeoJSON data
- `department_restaurants.geojson` - Restaurant data and departmental GeoJSON data.
- `region_restaurants.geojson` - Restaurant data and regional GeoJSON data.

---