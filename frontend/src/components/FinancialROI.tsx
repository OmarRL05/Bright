"use client";

import { useState } from "react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts";

export default function FinancialROI() {
  // Variables interactivas para el análisis de sensibilidad
  const [fuelPrice, setFuelPrice] = useState<number>(24.5); // Precio por litro
  const [didiCommission, setDidiCommission] = useState<number>(25); // % de comisión tradicional
  const [iaEfficiency, setIaEfficiency] = useState<number>(20); // % de ahorro en distancia/tiempo

  // Supuestos del modelo (Jornada de 8 horas)
  const baseDeliveries = 25;
  const payPerDelivery = 85;
  const baseDistanceKm = 150; // Recorrido empírico sin optimizar
  const vehicleKmPerLiter = 12; // Rendimiento promedio en ciudad

  // Cálculos Financieros: Baseline (Modelo DiDi/Tradicional)
  const baselineGross = baseDeliveries * payPerDelivery;
  const baselineCommission = baselineGross * (didiCommission / 100);
  const baselineFuelCost = (baseDistanceKm / vehicleKmPerLiter) * fuelPrice;
  const baselineNet = baselineGross - baselineCommission - baselineFuelCost;

  // Cálculos Financieros: Agente IA (The Courier)
  // La IA permite hacer más entregas en el mismo tiempo al optimizar rutas
  const iaDeliveries = Math.floor(baseDeliveries * (1 + iaEfficiency / 100));
  const iaGross = iaDeliveries * payPerDelivery;
  // Supongamos que The Courier cobra un SaaS fijo o una comisión hiper-baja (ej. 5%)
  const iaCommission = iaGross * 0.05; 
  // La IA reduce la distancia recorrida para entregar más
  const iaDistanceKm = baseDistanceKm * (1 - iaEfficiency / 100);
  const iaFuelCost = (iaDistanceKm / vehicleKmPerLiter) * fuelPrice;
  const iaNet = iaGross - iaCommission - iaFuelCost;

  // Formateo de datos estricto para Recharts
  const chartData = [
    {
      name: "Ingreso Bruto",
      Baseline: parseFloat(baselineGross.toFixed(2)),
      "The Courier": parseFloat(iaGross.toFixed(2)),
    },
    {
      name: "Comisiones (Fuga)",
      Baseline: parseFloat(baselineCommission.toFixed(2)),
      "The Courier": parseFloat(iaCommission.toFixed(2)),
    },
    {
      name: "Gasto Gasolina",
      Baseline: parseFloat(baselineFuelCost.toFixed(2)),
      "The Courier": parseFloat(iaFuelCost.toFixed(2)),
    },
    {
      name: "Ganancia NETA",
      Baseline: parseFloat(baselineNet.toFixed(2)),
      "The Courier": parseFloat(iaNet.toFixed(2)),
    },
  ];

  return (
    <div className="flex flex-col gap-4 rounded-lg border border-gray-200 bg-white p-6 shadow-sm dark:border-gray-800 dark:bg-zinc-950">
      <div className="flex flex-col justify-between gap-4 border-b border-gray-200 pb-4 md:flex-row md:items-center dark:border-gray-800">
        <div>
          <h2 className="text-lg font-bold text-gray-900 dark:text-gray-100">
            Análisis de Rentabilidad (Unit Economics)
          </h2>
          <p className="text-sm text-gray-500">
            Impacto financiero de la optimización de rutas vs. Plataformas Tradicionales
          </p>
        </div>
        <div className="flex flex-col items-end">
          <span className="text-xs font-medium text-gray-500 uppercase tracking-wider">
            Ventaja Competitiva Neta
          </span>
          <span className="text-2xl font-black text-emerald-500">
            +${(iaNet - baselineNet).toFixed(2)} <span className="text-sm">/ turno</span>
          </span>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* Controles Interactivos (Análisis de Sensibilidad) */}
        <div className="flex flex-col gap-6 rounded-md bg-gray-50 p-4 dark:bg-zinc-900">
          <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300">
            Simulador de Escenarios
          </h3>

          <div className="flex flex-col gap-2">
            <label className="flex justify-between text-xs font-medium text-gray-600 dark:text-gray-400">
              <span>Precio Gasolina ($/L)</span>
              <span>${fuelPrice.toFixed(2)}</span>
            </label>
            <input
              type="range"
              min="18"
              max="35"
              step="0.5"
              value={fuelPrice}
              onChange={(e) => setFuelPrice(parseFloat(e.target.value))}
              className="h-2 w-full cursor-pointer appearance-none rounded-lg bg-gray-200 accent-emerald-500 dark:bg-gray-700"
            />
          </div>

          <div className="flex flex-col gap-2">
            <label className="flex justify-between text-xs font-medium text-gray-600 dark:text-gray-400">
              <span>Comisión DiDi/Uber (%)</span>
              <span>{didiCommission}%</span>
            </label>
            <input
              type="range"
              min="10"
              max="40"
              step="1"
              value={didiCommission}
              onChange={(e) => setDidiCommission(parseFloat(e.target.value))}
              className="h-2 w-full cursor-pointer appearance-none rounded-lg bg-gray-200 accent-amber-500 dark:bg-gray-700"
            />
          </div>

          <div className="flex flex-col gap-2">
            <label className="flex justify-between text-xs font-medium text-gray-600 dark:text-gray-400">
              <span>Eficiencia IA The Courier (%)</span>
              <span className="text-emerald-500">+{iaEfficiency}%</span>
            </label>
            <input
              type="range"
              min="5"
              max="50"
              step="1"
              value={iaEfficiency}
              onChange={(e) => setIaEfficiency(parseFloat(e.target.value))}
              className="h-2 w-full cursor-pointer appearance-none rounded-lg bg-gray-200 accent-emerald-500 dark:bg-gray-700"
            />
            <p className="mt-1 text-[10px] text-gray-500 leading-tight">
              A mayor eficiencia, se reduce el kilometraje inútil y permite completar más pedidos en la misma jornada.
            </p>
          </div>
        </div>

        {/* Gráfico Recharts */}
        <div className="h-72 w-full lg:col-span-2">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart
              data={chartData}
              margin={{ top: 10, right: 10, left: -20, bottom: 0 }}
            >
              <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#374151" opacity={0.3} />
              <XAxis 
                dataKey="name" 
                axisLine={false} 
                tickLine={false} 
                tick={{ fontSize: 12, fill: '#6b7280' }} 
              />
              <YAxis 
                axisLine={false} 
                tickLine={false} 
                tick={{ fontSize: 12, fill: '#6b7280' }}
                tickFormatter={(value) => `$${value}`}
              />
              <Tooltip
                cursor={{ fill: 'rgba(255, 255, 255, 0.05)' }}
                contentStyle={{ backgroundColor: '#18181b', borderColor: '#27272a', color: '#fff', borderRadius: '8px' }}
                itemStyle={{ fontWeight: 'bold' }}
                formatter={(value) => {
                  const numericValue = Array.isArray(value) ? Number(value[0] ?? 0) : Number(value ?? 0);
                  return [`$${numericValue.toFixed(2)}`, "Monto"];
                }}
              />
              <Legend wrapperStyle={{ fontSize: '12px', paddingTop: '10px' }} />
              <Bar dataKey="Baseline" fill="#f59e0b" radius={[4, 4, 0, 0]} name="Modelo DiDi" />
              <Bar dataKey="The Courier" fill="#10b981" radius={[4, 4, 0, 0]} name="IA The Courier" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}