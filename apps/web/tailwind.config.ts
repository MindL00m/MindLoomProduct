import type { Config } from "tailwindcss";

/**
 * Loom palette.
 *   brand (logo teal)  #2a9d8f
 *   abyss / deep       #003135 / #024950 (brand-900 / brand-800)
 *   sky                #afdde5 (brand-200)
 *   rust               #964734
 *
 * Semantic tokens (background / foreground / border / ...) are exposed as CSS
 * variables in src/index.css so components stay theme-agnostic.
 */
const config: Config = {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    container: {
      center: true,
      padding: "2rem",
      screens: { "2xl": "1400px" },
    },
    extend: {
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        success: {
          DEFAULT: "hsl(var(--success))",
          foreground: "hsl(var(--success-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        // Raw palette, available as e.g. bg-brand, text-mist-700.
        brand: {
          DEFAULT: "#2a9d8f",
          50: "#eef8f9",
          100: "#d7eff2",
          200: "#afdde5",
          300: "#7cc7c9",
          400: "#4fb2ad",
          500: "#2a9d8f",
          600: "#218276",
          700: "#1a675f",
          800: "#024950",
          900: "#003135",
        },
        rust: {
          DEFAULT: "#964734",
          50: "#f8eeeb",
          100: "#f0d9d3",
          500: "#964734",
          600: "#7d3b2b",
          700: "#5f2d21",
        },
        mist: {
          200: "#d5e3e5",
          400: "#9bb3b6",
          700: "#4b6b6e",
        },
      },
      borderRadius: {
        lg: "16px",
        md: "12px",
        sm: "8px",
      },
      fontFamily: {
        sans: [
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "sans-serif",
        ],
      },
      keyframes: {
        "fade-in": {
          from: { opacity: "0" },
          to: { opacity: "1" },
        },
        shimmer: {
          "100%": { transform: "translateX(100%)" },
        },
      },
      animation: {
        "fade-in": "fade-in 0.3s ease-out",
        shimmer: "shimmer 1.6s infinite",
      },
    },
  },
  plugins: [],
};

export default config;
