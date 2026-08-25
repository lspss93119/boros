import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { App } from "./App";

function renderApp(hostname: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <App hostname={hostname} />
    </QueryClientProvider>,
  );
}

describe("unified dashboard areas", () => {
  it("shows static-mode local monitor copy but keeps research areas", () => {
    renderApp("boros.pages.dev");
    expect(screen.getAllByText("Local monitor not connected")).toHaveLength(3);
    expect(screen.getByRole("link", { name: /market radar/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /historical apr/i })).toBeInTheDocument();
  });

  it("contains no execution controls", () => {
    renderApp("localhost");
    expect(screen.queryByRole("button", { name: /trade|close|roll|execute/i })).toBeNull();
  });
});
