import plotly.express as px
import plotly.graph_objects as go
import json


def plot_interactive_department(data_df, geo_df, department_code):
    # Initialize a blank figure
    fig = go.Figure()

    # Get the specific geometry
    specific_geometry = geo_df[geo_df['code'] == str(department_code)]['geometry'].iloc[0]

    # Plot the geometry's boundaries
    if specific_geometry.geom_type == 'Polygon':
        x, y = specific_geometry.exterior.xy
        fig.add_trace(go.Scattermapbox(
            lat=list(y),
            lon=list(x),
            mode='lines',
            line=dict(width=0.5, color='black'),  # Making line thicker and black for visibility
            hoverinfo='none',
            showlegend=False  # Hide from legend
        ))
    elif specific_geometry.geom_type == 'MultiPolygon':
        for polygon in specific_geometry.geoms:
            if polygon.geom_type == 'Polygon':  # Ensure we're dealing with a Polygon
                x, y = polygon.exterior.xy
                fig.add_trace(go.Scattermapbox(
                    lat=list(y),
                    lon=list(x),
                    mode='lines',
                    line=dict(width=0.5, color='black'),
                    hoverinfo='none',
                    showlegend=False
                ))

    # Define custom color map based on stars
    color_map = {1: "yellow", 2: "orange", 3: "red"}
    dept_data = data_df[(data_df['department_num'] == str(department_code)) & (data_df['stars'] != 0.5)].copy()
    dept_data['color'] = dept_data['stars'].map(color_map)

    # Construct hover text with clickable URL and repeated star emojis
    dept_data['hover_text'] = dept_data.apply(lambda row: f"<b>{row['name']}</b><br>{'⭐' * int(row['stars'])}<br>"
                                                          f"Location: {row['location']}<br>Cuisine: {row['cuisine']}<br>"
                                                          f"<a href='{row['url']}' target='_blank'>Visit website</a><br>"
                                                          f"Price: {row['price']}",
                                              axis=1)

    # Overlay restaurant points
    for star, color in color_map.items():
        subset = dept_data[dept_data['stars'] == star]
        fig.add_trace(go.Scattermapbox(lat=subset['latitude'],
                                       lon=subset['longitude'],
                                       mode='markers',
                                       marker=go.scattermapbox.Marker(size=10, color=color),
                                       text=subset['hover_text'],
                                       hovertemplate='%{text}<br>Coordinates: (%{lat}, %{lon})',
                                       name=f"{'⭐' * int(star)}"))

    # Adjusting layout
    fig.update_layout(
        plot_bgcolor='rgba(230, 230, 230, 0.7)',
        paper_bgcolor='rgba(230, 230, 230, 0.7)',
        width=1000,
        height=800,
        mapbox_style="carto-positron",
        mapbox_zoom=8,
        mapbox_center_lat=dept_data['latitude'].mean(),
        mapbox_center_lon=dept_data['longitude'].mean()
    )

    return fig


