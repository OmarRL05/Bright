"""Descarga y precalcula el grafo vial de Monterrey (backend/data/monterrey.graphml).

Correr una sola vez (manualmente), no como parte de la suite de tests:
    python backend/data/datacreate.py

El archivo resultante NO se commitea (ver backend/data/README.md y .gitignore).
"""

import os

import osmnx as ox

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "monterrey.graphml")


def main() -> None:
    print("Descargando grafo vial de Monterrey desde OpenStreetMap...")
    graph = ox.graph_from_place("Monterrey, Nuevo Leon, Mexico", network_type="drive")

    print(f"Nodos: {graph.number_of_nodes()}, aristas: {graph.number_of_edges()}")

    print("Calculando velocidades y tiempos de viaje por arista...")
    graph = ox.routing.add_edge_speeds(graph)
    graph = ox.routing.add_edge_travel_times(graph)

    ox.save_graphml(graph, OUTPUT_PATH)
    print(f"Guardado en {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
