# Michelin Rated Restaurants in France

![Michelin Star](Images/Etoile_Michelin.svg)

----

## Overview

This project presents an analysis of Michelin rated restaurants in France. It leverages diverse datasets such as; restaurant details from the Michelin Guide, demographic data from INSEE, and the geospatial data of France. 

We investigate the distribution of these elite establishments and analyse if there's any correlation with the demographic attributes of their locations.

----

## Data Sources

1. **[Michelin Guide Restaurants](https://www.kaggle.com/datasets/ngshiheng/michelin-guide-restaurants-2021):** The dataset, updated quarterly, comprises restaurant details such as address, price range, cuisine type, longitude, latitude, and Michelin rating (3 stars, 2 stars, 1 star, Bib Gourmand). Data was fetched on 4th August 2023.

2. **[INSEE](https://www.insee.fr/fr/accueil) *(L'Institut national de la statistique et des études économiques):*** Demographic data for the year 2020 was procured from INSEE. It includes regional and departmental population and population density statistics.

3. **[Geospatial data](https://france-geojson.gregoiredavid.fr):** French departmental and regional geospatial data were retrieved as open license GeoJSON files.

----

## Data Aggregation and Processing

The processing leverages the French postal code's peculiarity, whereby the initial two digits denote a specific department. This information, extracted from the address column of the Michelin dataset, facilitated subdivision at the regional and departmental level.

Data processing involved three Jupyter notebooks:

1. [`Data-Preparation.ipynb`](Notebooks/Data-Preparation.ipynb): The Michelin Guide data was partitioned by country to isolate the data relevant to France. The processed dataset `france_data.csv` was subsequently exported.

2. [`France_Regions.ipynb`](Notebooks/France/France_Regions.ipynb): This notebook was used to aggregate demographic data from different sources. Departmental and regional data were scraped from Wikipedia, whereas population statistics were fetched from INSEE. Departmental area was computed from the population density and total population data. The output `demographics.csv` was exported.

3. [`France_Processing.ipynb`](Notebooks/France/France_Processing.ipynb): This notebook performs exploratory data analysis and further processing. It outlines the steps to merge the restaurant data with the demographic and geospatial JSON data.

After the processing, the following datasets were exported:

- `all_restaurants.csv` - Michelin restaurant data appended with regional information.
- `department_restaurants.geojson` - A combination of Michelin data and departmental GeoJSON data.
- `region_restaurants.geojson` - A combination of Michelin data and regional GeoJSON data.

----

## Visualisations

[`France_Visualisations.ipynb`](Notebooks/France/France_Visualisations.ipynb)

...

----

## Analysis

[`France_Analysis.ipynb`](Notebooks/France/France_Analysis.ipynb)

...

----

### Custom [Functions](Notebooks/France/functions_visualisation.py)

The `top_restaurants_by_department` function analyses a dataset of restaurants and outputs the top $n$ departments with the highest count of restaurants with a specified Michelin star rating. This function can be used on a larger scale, analyzing countrywide data or on a smaller scale, analyzing city-specific data like restaurants in Paris.

----