import { cleanup, render, screen } from "@testing-library/react"
import { createMemoryRouter, RouterProvider } from "react-router"
import { afterEach, describe, expect, it, vi } from "vitest"

import { ErroDeTela } from "./ErroDeTela"

function TelaQuebrada({ erro }: { erro: Error }): never {
  throw erro
}

function abrir(erro: Error) {
  vi.spyOn(console, "error").mockImplementation(() => {}) // o React e a própria tela registram o erro
  const router = createMemoryRouter([{ path: "/", element: <TelaQuebrada erro={erro} />, errorElement: <ErroDeTela /> }])
  render(<RouterProvider router={router} />)
}

describe("ErroDeTela", () => {
  afterEach(cleanup)

  it("troca a página padrão (em inglês, com stack trace) por uma mensagem em português", () => {
    abrir(new TypeError("Cannot read properties of undefined (reading 'nome')"))
    expect(screen.getByRole("alert").textContent).toContain("Algo deu errado nesta tela")
    expect(screen.queryByText(/Cannot read properties/)).toBeNull() // o detalhe técnico não aparece
    expect(screen.getByRole("button", { name: /Recarregar/ })).toBeTruthy()
    expect(screen.getByRole("link", { name: "Ir para os projetos" }).getAttribute("href")).toBe("/projetos")
  })

  it("depois de um deploy, pede para recarregar (a aba pede arquivos da versão antiga)", () => {
    abrir(new TypeError("Failed to fetch dynamically imported module: /assets/Projetos-abc123.js"))
    expect(screen.getByRole("alert").textContent).toContain("O sistema foi atualizado")
  })
})
