# Michelin Rated Restaurants in France: An Analysis
![Michelin Star](Images/Etoile_Michelin.svg)

---

## Overview
This project is multifaceted. While it showcases proficiency in data acquisition, processing, and visualisation, it also delves into the world of Michelin-rated restaurants in France. Leveraging datasets from the Michelin Guide and INSEE, along with French geospatial data, this analysis probes the correlation between elite restaurants and the socio-economic attributes of their surroundings.

In a previous life I was a Michelin trained chef and lived in France for six transformative years. Those years, rich from the diverse cultural tapestry of France, have instilled in me a deep appreciation for the intricate world of gastronomy and its profound influence on local cultures. This project is not just an analytical endeavor but a personal journey. Through it, I seek to marry my experiences with my current passion for data, aiming to uncover insights that resonate on both a professional and personal level.


---

## Project Objective
*"How do Michelin-starred establishments correlate with the socio-economic metrics of their respective regions?"* 

The aim is to better understand the intricate relationship between culinary excellence, its geographical distribution, and the socio-economic fabric of France.

---

## Data Sources
- [Michelin Guide Restaurants](https://www.kaggle.com/datasets/ngshiheng/michelin-guide-restaurants-2021)
- [INSEE](https://www.insee.fr/fr/accueil)
- [Geospatial data](https://france-geojson.gregoiredavid.fr)

---

## Data Aggregation and Processing
The processing leverages the French postal code's peculiarity, whereby the initial two digits denote a specific department. This information, extracted from the address column of the Michelin dataset, facilitated subdivision at the regional and departmental level.

Data processing involved three Jupyter notebooks:

1. [`Data-Preparation.ipynb`](Notebooks/Data-Preparation.ipynb): The Michelin Guide data was partitioned by country to isolate the data relevant to France. The processed dataset `france_data.csv` was subsequently exported.

2. [`France_Departments_Regions.ipynb`](Notebooks/France/France_Departments_Regions.ipynb): This notebook was used to aggregate socio-economic data from different sources. Departmental and regional data were scraped from Wikipedia, whereas population statistics were fetched from INSEE. Departmental area was computed from the population density and total population data. The output `demographics.csv` was exported.

3. [`France_Arrondissement.ipynb`](Notebooks/France/France_Arrondissements.ipynb): The data was further granulated to *arrondissement* level

   - `arrondissement_restaurants.geojson` - A combination of Michelin data and arrondissement GeoJSON data was exported

4. [`France_Processing.ipynb`](Notebooks/France/France_Processing.ipynb): This notebook performs exploratory data analysis and further processing. It outlines the steps to merge the restaurant data with the demographic and geospatial JSON data.

After the processing, the following datasets were exported:

- `all_restaurants.csv` - Michelin restaurant data appended with regional information.
- `department_restaurants.geojson` - A combination of Michelin data and departmental GeoJSON data.
- `region_restaurants.geojson` - A combination of Michelin data and regional GeoJSON data.

---

## Visualisations
Intricate functions are presented and [defined](Functions/functions_visualisation.py) to query and represent the data in diverse and insightful manners. Discover the explorations in the [`France_Visualisations.ipynb`](Notebooks/France/France_Visualisations.ipynb) notebook.

---

## Interactive App
An interactive application built using Dash and Plotly provides a dynamic interface for users to explore Michelin-rated establishments in France. Users can select specific regions, zoom into departments, and view detailed restaurant information.

Access the app: [Michelin France Interactive Map](https://michelin-france-2ed085da7e1c.herokuapp.com)

---

## Analysis
The analysis segment of this project is designed for both technical and non-technical audiences. Utilizing the functions from the visualisation phase, this section endeavors to answer pertinent questions with minimal code to ensure readability and comprehension.

Explore the detailed analysis in the [`France_Analysis.ipynb`](Notebooks/France/France_Analysis.ipynb) notebook.

---

## Future Updates
Given the Michelin Guide's yearly release schedule, this project intends to periodically update the analysis to reflect the latest trends and insights. Automation structures for these updates are currently under consideration.

---

