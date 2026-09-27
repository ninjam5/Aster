import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Home } from "lucide-react";
import { Button } from "./Button";
import { Toggle } from "./Toggle";
import { NavItem } from "./NavItem";
import { Chip } from "./Chip";
import { EmptyState } from "./EmptyState";

describe("Button", () => {
  it("renders children and applies the primary variant by default", () => {
    render(<Button>Save</Button>);
    expect(screen.getByRole("button", { name: "Save" })).toHaveClass("bg-accent-blue");
  });

  it("fires onClick", async () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick}>Go</Button>);
    await userEvent.click(screen.getByRole("button", { name: "Go" }));
    expect(onClick).toHaveBeenCalledOnce();
  });
});

describe("Toggle", () => {
  it("reflects checked state and toggles on click", async () => {
    const onChange = vi.fn();
    render(<Toggle checked={false} onChange={onChange} label="Vision" />);
    const sw = screen.getByRole("switch", { name: "Vision" });
    expect(sw).toHaveAttribute("aria-checked", "false");
    await userEvent.click(sw);
    expect(onChange).toHaveBeenCalledWith(true);
  });
});

describe("NavItem", () => {
  it("marks the active item and calls onClick", async () => {
    const onClick = vi.fn();
    render(<NavItem icon={Home} label="Home" active onClick={onClick} />);
    const btn = screen.getByRole("button", { name: /Home/ });
    expect(btn).toHaveClass("bg-accent-blue/15");
    await userEvent.click(btn);
    expect(onClick).toHaveBeenCalledOnce();
  });
});

describe("Chip & EmptyState", () => {
  it("renders chip content", () => {
    render(<Chip tone="success">Connected</Chip>);
    expect(screen.getByText("Connected")).toBeInTheDocument();
  });

  it("renders empty-state title and description", () => {
    render(<EmptyState icon={Home} title="Nothing here" description="Try again" />);
    expect(screen.getByText("Nothing here")).toBeInTheDocument();
    expect(screen.getByText("Try again")).toBeInTheDocument();
  });
});
