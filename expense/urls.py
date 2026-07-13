from django.urls import path
from .views import CreateExpenseView, GroupBalancesView, ExpenseDetailView, PersonalExpenseListCreateView, PersonalExpenseDetailView, DashboardAnalyticsView, DashboardAiInsightsView

urlpatterns = [
    # Matches: POST /api/expenses/group/<group_id>/
    path('group/<int:group_id>/', CreateExpenseView.as_view(), name='create-expense'),
    
    # Matches: GET /api/expenses/group/<group_id>/balances/
    path('group/<int:group_id>/balances/', GroupBalancesView.as_view(), name='group-balances'),
    
    # Matches: PATCH /api/expenses/<expense_id>/, PUT /api/expenses/<expense_id>/, DELETE /api/expenses/<expense_id>/
    path('<int:expense_id>/', ExpenseDetailView.as_view(), name='expense-detail'),
    # 🛍️ Isolated Personal Tracking Engine
    path('personal/', PersonalExpenseListCreateView.as_view(), name='personal-expense-list-create'),
    path('personal/<int:pk>/', PersonalExpenseDetailView.as_view(), name='personal-expense-detail'),

    # 📊 Aggregated Dashboard Endpoint
    path('dashboard/', DashboardAnalyticsView.as_view(), name='dashboard-analytics'),
    path('dashboard/ai-insights/', DashboardAiInsightsView.as_view(), name='dashboard-ai-insights'),
]