import type { Metadata } from "next";
import { Archivo, IBM_Plex_Mono } from "next/font/google";
import "./globals.css";

/*
 * Archivo para la interfaz, IBM Plex Mono para lo que dice la maquina.
 *
 * No son las Geist del scaffold. Archivo es un grotesco de la tradicion de
 * señaletica: formas apretadas, ojo grande, aguanta pesos altos en cifras
 * pequeñas -- que es literalmente lo que hace una consola de despacho.
 *
 * La mono no es decorativa y no se usa "para datos" en general: marca lo que
 * viene LITERAL del contrato -- order_id, binding_constraint, latencia, seed,
 * hora de simulacion. Por eso tiene que verse de otra familia de un vistazo;
 * con dos grotescos parecidos la distincion se pierde y la mono pasa a ser
 * textura.
 */
const archivo = Archivo({
  variable: "--font-archivo",
  subsets: ["latin"],
  display: "swap",
});

const plexMono = IBM_Plex_Mono({
  variable: "--font-plex-mono",
  subsets: ["latin"],
  weight: ["400", "500"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "The Courier — consola de despacho",
  description:
    "Agente de decisión para repartidores: la seguridad está en el código y cada rechazo dice qué regla lo paró.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    // `lang="es"` y no "en": toda la interfaz esta en español y un lector de
    // pantalla estaba pronunciandola con fonemas ingleses.
    <html
      lang="es"
      className={`${archivo.variable} ${plexMono.variable} h-full antialiased`}
    >
      <body className="min-h-full">{children}</body>
    </html>
  );
}
