import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MarkdownText } from "./MarkdownText";

describe("安全 Markdown 文本", () => {
  it("渲染标题、加粗、代码和列表，但不执行原始 HTML", () => {
    render(<MarkdownText content={"### 关键步骤\n\n**检索** `top_k=2`\n\n- 找到文档\n- 生成回答\n\n<script>alert(1)</script>"} />);

    expect(screen.getByRole("heading", { name: "关键步骤", level: 3 })).toBeInTheDocument();
    expect(screen.getByText("检索").tagName).toBe("STRONG");
    expect(screen.getByText("top_k=2").tagName).toBe("CODE");
    expect(screen.getByRole("list")).toHaveTextContent("找到文档生成回答");
    expect(screen.getByText("<script>alert(1)</script>")).toBeInTheDocument();
  });
});
