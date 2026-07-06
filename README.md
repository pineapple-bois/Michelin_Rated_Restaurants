# Michelin Rated Restaurants in France: An Analysis
![Michelin Star](Images/Etoile_Michelin.svg)

---

## Overview
This project is multifaceted. While it showcases proficiency in data acquisition, processing, and visualisation, it also delves into the world of Michelin-rated restaurants in France. Leveraging datasets from the Michelin Guide and INSEE, along with French geospatial data, this analysis probes the correlation between elite restaurants and the socio-economic attributes of their surroundings.

Previously I was a Michelin trained chef and lived in France for six transformative years. Those years instilled in me a deep appreciation for the world of gastronomy and its profound influence on local culture. Through this project, I seek to marry my life experiences with my current passion for data.


---

## Project Objective

This analysis seeks to address the question:

*"How do Michelin-starred establishments correlate with the socio-economic metrics of their respective regions?"* 

The aim is to better understand the intricate relationship between culinary excellence, its geographical distribution, and the socio-economic fabric of France.

---

## Data Sources
#### [Michelin Guide Restaurants](https://www.kaggle.com/datasets/ngshiheng/michelin-guide-restaurants-2021) 

- Sourced from Kaggle

#### [INSEE - (Institut National de la Statistique et des Études Économiques)](https://statistiques-locales.insee.fr/#c=home) 

- Demographic and socio-economic data was sourced from the National Institute of Statistics and Economic Studies. 
- INSEE is responsible for the production and analysis of official French statistics.

#### [France-GeoJSON](https://france-geojson.gregoiredavid.fr)

- The geospatial data used in this analysis. 
- We acknowledge and thank the contributors and maintainers of this resource for making it publicly available.

---

## [Processing Pipeline (by year)](Years)
Data is from the above sources is merged, transformed and exported.

---

## [Tracking the changes by year](https://github.com/pineapple-bois/Michelin_Rated_Restaurants/blob/main/Years/2024/Notebooks/France/France_Changes.ipynb) 
Finding and logging significant changes in star ratings.
- Currently [2024](https://github.com/pineapple-bois/Michelin_Rated_Restaurants/tree/main/Years/2024) compared to [2023](https://github.com/pineapple-bois/Michelin_Rated_Restaurants/tree/main/Years/2023)

---

## [Visualisations](Years/2023/Notebooks/France/France_Visualisations.ipynb)
Intricate functions are presented and [defined](Functions/functions_visualisation.py) to query and represent the data in diverse and insightful manners. 

---

## [Analysis](Years/2023/Notebooks/France/France_Analysis.ipynb)
The analysis segment of this project is designed for both technical and non-technical audiences. Utilizing the functions from the visualisation phase, this section endeavors to answer pertinent questions with minimal code to ensure readability and comprehension.

---

## [Interactive Application](https://www.restaurant-guide-france.net)
An interactive application built using Dash and Plotly provides a dynamic interface for users to explore Michelin-rated establishments in France. Users can select specific regions, zoom into departments, and view detailed restaurant information.

### [Access the source code:](https://github.com/pineapple-bois/Michelin_App_Development)

----

## Repository Structure

```
├── ExtraData
│   ├── Demographics
│   ├── Geodata
│   └── Wine
├── Functions
├── Images
├── Years
│   ├── 2023
│   │   ├── data
│   │   │   ├──France
│   │   │   ├──Michelin
│   │   │   └──UK
│   │   ├── Notebooks
│   │   │   ├──France
│   │   │   └──Uk
│   ├── 2024
│   │   ├── data
│   │   │   ├──France
│   │   │   ├──Michelin
│   │   │   └──UK
│   │   ├── Notebooks
│   │   │   ├──France
│   │   │   └──Uk
├── README.md
```
---

## Installation Guide

### 1. Clone the Repository
```bash
git clone https://github.com/pineapple-bois/Michelin_Rated_Restaurants.git
cd Michelin_Rated_Restaurants
```

### 2. Create and Activate a Virtual Environment
```bash
python3 -m venv env
source env/bin/activate
```

### 3. Install the required packages
```bash
pip install -r requirements.txt
```

### 4. Deactivate the virtual environment (when done)
```bash
deactivate
```

## Stage 1 country partitions

Stage 1 is implemented as reusable Python code in `src/data_pipeline/stage1/`.
It turns an accepted local raw snapshot into validated France, Monaco, and UK
partitions.

Build canonical partitions:

```bash
PYTHONPATH=src python -m data_pipeline partition --year 2026
```

Build a disposable candidate:

```bash
PYTHONPATH=src python -m data_pipeline partition \
  --year 2026 \
  --output-root /tmp/michelin-stage1-2026
```

Validate without publishing, or deliberately rebuild an existing year:

```bash
PYTHONPATH=src python -m data_pipeline partition --year 2026 --validate-only
PYTHONPATH=src python -m data_pipeline partition --year 2026 --replace
```

Existing outputs are protected unless `--replace` is supplied. Replacement is
attempted only after all three outputs validate and stage successfully.

Run the automated tests:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

See [`docs/stage1.md`](docs/stage1.md) for the notebook-to-Python mapping,
schemas, validation rules, historical fidelity, and publication behavior.

## Stage 2 France geographic products

Build the enriched France restaurant CSV plus departmental and regional GeoJSON:

```bash
PYTHONPATH=src python -m data_pipeline departments --year 2026
```

Use `--output-root /tmp/michelin-stage2-2026` for a candidate,
`--validate-only` for a no-write check, or `--replace` for a deliberate rebuild.
Canonical products are stored under `data/products/france/<year>/`.

See [`docs/stage2-france-departments.md`](docs/stage2-france-departments.md)
for inputs, notebook mapping, schemas, validation, fidelity, and publication.

Build Monaco application products with the same candidate, validation, and
replacement conventions:

```bash
PYTHONPATH=src python -m data_pipeline monaco --year 2026
PYTHONPATH=src python -m data_pipeline monaco --year 2026 --validate-only
```

The detailed Stage 2 document also describes Monaco's synthetic application
code and placeholder demographic fields.

----
