from django.urls import path
from .views import RecordSettlementView, SettlementDetailView

urlpatterns = [
    # GET (history) or POST (new payment)
    path('group/<int:group_id>/', RecordSettlementView.as_view(), name='record-settlement'),
    
    # DELETE a settlement record
    path('<int:settlement_id>/', SettlementDetailView.as_view(), name='settlement-detail'),
]