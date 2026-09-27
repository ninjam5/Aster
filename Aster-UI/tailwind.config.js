/** @type {import('tailwindcss').Config} */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        background: "#111111",
        "surface-card": "#1E1E1E",
        "surface-elevated": "#2A2A2A",
        "outline-custom": "#3A3A3A",
        primary: "#a5caf4",
        "accent-blue": "#5B7FA6",
        "on-surface": "#e2e2e5",
        "on-surface-variant": "#c2c7cf",
        "error-container": "#3D1F1F",
        "error-text": "#ffb4ab",
        // Brand accents for Connected Socials — distinct per platform, not in Tailwind's default palette.
        "social-spotify": "#1DB954",
        "social-discord": "#5865F2",
        "social-telegram": "#29A9EB",
      },
      borderRadius: {
        card: "12px",
        input: "8px",
      },
      spacing: {
        margin_edge: "16px",
        touch_target: "48px",
        button_height: "52px",
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
      },
      fontSize: {
        "headline-lg": ["24px", { lineHeight: "32px", letterSpacing: "-0.02em", fontWeight: "700" }],
        "headline-md": ["20px", { lineHeight: "28px", letterSpacing: "-0.01em", fontWeight: "600" }],
        "body-md": ["14px", { lineHeight: "20px", fontWeight: "400" }],
        "label-lg": ["14px", { lineHeight: "20px", letterSpacing: "0.1px", fontWeight: "500" }],
      },
    },
  },
  plugins: [],
};
