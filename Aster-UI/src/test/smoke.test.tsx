import { render, screen } from "@testing-library/react";
import App from "../App";

describe("toolchain smoke", () => {
  it("renders the app entry without crashing", () => {
    render(<App />);
    // Fresh profile → onboarding welcome screen is shown.
    expect(screen.getByText("Welcome to the Aster framework")).toBeInTheDocument();
  });
});
