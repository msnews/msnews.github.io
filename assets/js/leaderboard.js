function buildDataTable() {
    var leaderboard = [];  
    //Access the leaderboard of phase 1 of the competition 1.
    leaderboard.push(getJson("http://codalabtest.cloudapp.net/api/competition/1/phases/1/leaderboard/data")[0]);
    var tr;
    //clear the table
    $("#leaderboardtable").empty();
    var columnSet = [];
    var headerTr$ = $('<tr/>');
    //Get the header data
    var headers = leaderboard[0].headers;
    headerTr$.append($('<th/>').html("participant"));  
    //Populate the header in the leaderboardtable                  
    for (var j = 0; j < headers.length; j++) {
       headerTr$.append($('<th/>').html(headers[j].label)); 
    }   		
    $("#leaderboardtable").append(headerTr$);
    //Populate the scores in the leaderboardtable             
    for (var i = 0; i < leaderboard[0].scores.length; i++) {
              tr = $('<tr/>');
            tr.append("<td>" + leaderboard[0].scores[i][1].username + "</td>");
            values = leaderboard[0].scores[i][1].values;
            for (var j = 0; j < values.length; j++) {
                 tr.append("<td>" + values[j].val + "</td>");                        
            }
            $('#leaderboardtable').append(tr);
     }
  }