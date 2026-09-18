/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#0f0f12",
        panel: "#18181f",
        line: "#2a2a35",
        // Use DEFAULT so bg-violet / text-amber work without wiping out the
        // built-in violet-50..950 and amber-50..950 shades.
        violet: { DEFAULT: "#8b5cf6" },
        amber: { DEFAULT: "#f59e0b" },
        fog: "#9ca3af",
      },
    },
  },
  plugins: [],
};
