from django.urls import path
from .views import CreateExpenseView, GroupBalancesView, ExpenseDetailView

urlpatterns = [
    # Matches: POST /api/expenses/group/<group_id>/
    path('group/<int:group_id>/', CreateExpenseView.as_view(), name='create-expense'),
    
    # Matches: GET /api/expenses/group/<group_id>/balances/
    path('group/<int:group_id>/balances/', GroupBalancesView.as_view(), name='group-balances'),
    
    # Matches: PATCH /api/expenses/<expense_id>/, PUT /api/expenses/<expense_id>/, DELETE /api/expenses/<expense_id>/
    path('<int:expense_id>/', ExpenseDetailView.as_view(), name='expense-detail'),
]