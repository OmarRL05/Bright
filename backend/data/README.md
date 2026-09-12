# data/

Archivos pesados que NO se commitean a git (ver `.gitignore`):

- `monterrey.graphml` — grafo vial de Monterrey. Generar una vez con:
  ```python
  import osmnx as ox
  g = ox.graph_from_place("Monterrey, Nuevo Leon, Mexico", network_type="drive")
  ox.save_graphml(g, "backend/data/monterrey.graphml")
  ```
- `kaggle_orders.csv` — dataset de food-delivery de Kaggle usado para dar
  forma al stream de ofertas y a la señal de demanda histórica. Descargar
  manualmente y colocar aquí.

Comparte estos archivos por otro medio (Drive, Slack) para no inflar el repo.
