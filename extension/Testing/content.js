// Runs on Canvas and StudentVue pages
const BASE_URL = "https://intelli-plan.up.railway.app";

function addIntelliPlanButton() {
  // Don't add twice
  if (document.getElementById("intelliplan-btn")) return;

  const btn = document.createElement("a");
  btn.id = "intelliplan-btn";
  btn.href = BASE_URL + "/dashboard";
  btn.target = "_blank";
  btn.style.cssText = `
    position: fixed;
    bottom: 24px;
    right: 24px;
    z-index: 99999;
    background: #3b82f6;
    color: white;
    padding: 10px 16px;
    border-radius: 12px;
    font-family: -apple-system, sans-serif;
    font-size: 13px;
    font-weight: 600;
    text-decoration: none;
    display: flex;
    align-items: center;
    gap: 6px;
    box-shadow: 0 4px 20px rgba(59,130,246,0.4);
    transition: transform 0.2s, box-shadow 0.2s;
    cursor: pointer;
  `;
  btn.innerHTML = "📅 IntelliPlan";
  btn.onmouseover = () => {
    btn.style.transform = "translateY(-2px)";
    btn.style.boxShadow = "0 8px 28px rgba(59,130,246,0.5)";
  };
  btn.onmouseout = () => {
    btn.style.transform = "";
    btn.style.boxShadow = "0 4px 20px rgba(59,130,246,0.4)";
  };

  document.body.appendChild(btn);
}

// Wait for page to load
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", addIntelliPlanButton);
} else {
  addIntelliPlanButton();
}