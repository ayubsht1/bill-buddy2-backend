from django.urls import path
from .views import RecordSettlementView, SettlementDetailView

urlpatterns = [
    # GET (history) or POST (new payment)
    path('group/<uuid:group_id>/', RecordSettlementView.as_view(), name='record-settlement'),
    
    # DELETE a settlement record
    path('<uuid:settlement_id>/', SettlementDetailView.as_view(), name='settlement-detail'),
]